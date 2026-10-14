"""Small, authorized read-only tools. Model arguments never include identity."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.chat.domain import ConversationStatus
from app.models import Account, ChatConversation, Order, OrderStatus, Product
from app.permissions import PermissionCode
from app.read_queries import active_product_query, order_query
from app.security import account_query, permissions_for

ACTIVE_ORDER_STATUSES = (
    OrderStatus.PLACED,
    OrderStatus.CONFIRMED,
    OrderStatus.PROCESSING,
    OrderStatus.SHIPPED,
)


class AccessChanged(Exception):
    """Current permissions or conversation mode no longer allow AI processing."""


class ToolName(StrEnum):
    LIST_ORDERS = "list_orders"
    LOOKUP_ORDER = "lookup_order"
    SEARCH_PRODUCTS = "search_products"
    REQUEST_SUPPORT = "request_support"


class Arguments(BaseModel):
    # Validation only: reject unexpected tool arguments and trim string values.
    # The schemas below also enforce length and pagination limits before any tool runs.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ListOrders(Arguments):
    active_only: bool = True
    page: int = Field(default=1, ge=1, le=100)


class LookupOrder(Arguments):
    order_number: str = Field(min_length=1, max_length=32)


class SearchProducts(Arguments):
    query: str = Field(min_length=1, max_length=100)
    page: int = Field(default=1, ge=1, le=100)


class RequestSupport(Arguments):
    reason: str = Field(min_length=1, max_length=500)


TOOL_DEFINITIONS = {
    ToolName.LIST_ORDERS: (
        ListOrders,
        "List this customer's orders. Active means placed, confirmed, processing or shipped. Results are paginated.",
    ),
    ToolName.LOOKUP_ORDER: (
        LookupOrder,
        "Read this customer's order, shipment tracking, payments and status history by order number. No live carrier location or ETA is available.",
    ),
    ToolName.SEARCH_PRODUCTS: (
        SearchProducts,
        "Search active products by name or SKU for current USD price and stock availability. Ask the customer to choose if several match.",
    ),
    ToolName.REQUEST_SUPPORT: (
        RequestSupport,
        "Hand the conversation to a human support agent when requested or when the customer's issue cannot be handled with read-only tools.",
    ),
}


def tool_schemas() -> list[dict]:
    # Expose tool names, descriptions and argument schemas to the LLM. This does not
    # execute a tool; the graph passes the model's later tool calls to execute_tool().
    return [
        {"name": name.value, "description": description, "parameters": schema.model_json_schema()}
        for name, (schema, description) in TOOL_DEFINITIONS.items()
    ]


def current_customer(db: Session, account_id: uuid.UUID, conversation_id: uuid.UUID) -> Account:
    # Authorization validation only: read current account permissions and conversation
    # state. Nothing is changed here. Both IDs come from the server, not the LLM.
    account = db.scalar(account_query().where(Account.id == account_id))
    conversation = db.get(ChatConversation, conversation_id)
    required = {PermissionCode.USER_ACCESS, PermissionCode.CHAT_OWN}
    # Stop AI processing if access was revoked, ownership differs, or a human has
    # taken over. AccessChanged propagates to the service so it can cancel the run.
    if (
        account is None
        or not account.is_active
        or account.customer_id is None
        or not required <= permissions_for(account)
        or conversation is None
        or conversation.account_id != account.id
        or conversation.customer_id != account.customer_id
        or conversation.status != ConversationStatus.AI_ACTIVE
    ):
        raise AccessChanged()
    return account


def order_summary(order: Order) -> dict:
    # Serialize database values for the tool result; decimal money stays a string
    # to preserve its exact value when the graph encodes the result as JSON.
    return {
        "order_number": order.order_number,
        "status": order.status.value,
        "total": str(order.total_amount),
        "currency": "USD",
        "items": [
            {
                "name": item.product_name,
                "quantity": item.quantity,
                "unit_price": str(item.unit_price),
            }
            for item in order.items
        ],
    }


def execute_tool(
    engine: Engine, account_id: uuid.UUID, conversation_id: uuid.UUID, name: str, arguments: dict
) -> dict:
    # Validation only: model-generated tool names and arguments are untrusted.
    # Return a structured error to the graph without running an invalid request.
    if name not in TOOL_DEFINITIONS:
        return {"error": "unsupported_tool"}
    try:
        args = TOOL_DEFINITIONS[ToolName(name)][0].model_validate(arguments)
    except ValidationError:
        return {"error": "invalid_arguments"}
    # Each valid tool call uses a new session and rechecks authorization against the
    # database. Request-time authorization may have changed while the LLM was running.
    with Session(engine) as db:
        account = current_customer(db, account_id, conversation_id)
        if name in (ToolName.LIST_ORDERS, ToolName.LOOKUP_ORDER):
            # Authorization validation only: order tools require an additional grant.
            if PermissionCode.ORDERS_READ_OWN not in permissions_for(account):
                return {"error": "permission_denied"}
            # Customer-scoped read: always filter by the authenticated customer's ID.
            # Shared queries eagerly load order details needed to build the response.
            query = (
                order_query()
                .where(Order.customer_id == account.customer_id)
                .options(
                    selectinload(Order.status_history),
                )
            )
            if name == ToolName.LOOKUP_ORDER:
                # Search within that customer's orders, so an absent order and another
                # customer's order both produce the same not_found result.
                order = db.scalar(query.where(Order.order_number == args.order_number))
                if order is None:
                    return {"error": "not_found"}
                result = order_summary(order)
                # Read stored shipment, payment and status-history data. This does not
                # contact a carrier or payment provider, or change the order.
                result["shipments"] = [
                    {
                        "carrier": s.carrier,
                        "tracking_number": s.tracking_number,
                        "status": s.status.value,
                        "shipped_at": s.shipped_at.isoformat() if s.shipped_at else None,
                        "delivered_at": s.delivered_at.isoformat() if s.delivered_at else None,
                    }
                    for s in order.shipments
                ]
                result["payments"] = [
                    {"status": p.status.value, "amount": str(p.amount)} for p in order.payments
                ]
                result["history"] = [
                    {"status": h.to_status.value, "at": h.created_at.isoformat()}
                    for h in sorted(order.status_history, key=lambda x: x.created_at)
                ]
            else:
                # List orders in pages of ten, optionally limited to active statuses.
                # Count all matches before applying the page offset and limit.
                if args.active_only:
                    query = query.where(Order.status.in_(ACTIVE_ORDER_STATUSES))
                total = db.scalar(select(func.count()).select_from(query.subquery()))
                rows = db.scalars(
                    query.order_by(Order.created_at.desc(), Order.id)
                    .offset((args.page - 1) * 10)
                    .limit(10)
                )
                result = {
                    "items": [order_summary(o) for o in rows],
                    "total": total,
                    "page": args.page,
                    "page_size": 10,
                }
        elif name == ToolName.SEARCH_PRODUCTS:
            # Catalog read: search active products by name or SKU, escaping wildcard
            # characters so customer text is matched literally. Inventory is eager-loaded.
            query = active_product_query().where(
                or_(
                    Product.name.icontains(args.query, autoescape=True),
                    Product.sku.icontains(args.query, autoescape=True),
                ),
            )
            total = db.scalar(select(func.count()).select_from(query.subquery()))
            rows = db.scalars(
                query.order_by(Product.name, Product.id).offset((args.page - 1) * 10).limit(10)
            )
            result = {
                "items": [
                    {
                        "name": p.name,
                        "sku": p.sku,
                        "price": str(p.price),
                        "currency": "USD",
                        "in_stock": bool(p.inventory and p.inventory.quantity_available > 0),
                    }
                    for p in rows
                ],
                "total": total,
                "page": args.page,
                "page_size": 10,
            }
        else:
            # Signal a requested human handoff; no conversation state changes here.
            # The graph stops, and process_message() applies the handoff transaction.
            result = {"handoff_reason": args.reason}
        # Add retrieval time to successful results before the graph sends them to the
        # LLM, or uses a handoff_reason to end automated processing.
        return {**result, "retrieved_at": datetime.now(UTC).isoformat()}
