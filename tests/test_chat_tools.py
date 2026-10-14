import uuid

from sqlalchemy import select, text
from test_authorization import ids

from app.chat.tools import execute_tool
from app.models import Account, ChatConversation, Inventory, Product


def context(database):
    with database.begin() as db:
        account = db.scalar(select(Account).where(Account.email == "customer1@example.com"))
        conversation = ChatConversation(account_id=account.id, customer_id=account.customer_id)
        db.add(conversation)
        db.flush()
        return db.bind, account.id, conversation.id


def test_active_order_filter_and_order_permissions(seeded):
    engine, account_id, conversation_id = context(seeded)
    result = execute_tool(engine, account_id, conversation_id, "list_orders", {})
    assert all(
        o["status"] in ("placed", "confirmed", "processing", "shipped") for o in result["items"]
    )
    all_orders = execute_tool(
        engine, account_id, conversation_id, "list_orders", {"active_only": False}
    )
    assert all_orders["total"] == 3
    with seeded.begin() as db:
        db.execute(
            text(
                "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name='orders:read_own')"
            )
        )
    assert execute_tool(
        engine, account_id, conversation_id, "lookup_order", {"order_number": "ORD-100001"}
    ) == {"error": "permission_denied"}


def test_live_product_price_stock_and_inactive_visibility(seeded):
    engine, account_id, conversation_id = context(seeded)
    product_id = uuid.UUID(ids(seeded)["product"])
    with seeded.begin() as db:
        product = db.get(Product, product_id)
        product.name = "Unique search product"
        sku, price = product.sku, str(product.price)
        inventory = db.get(Inventory, product_id)
        inventory.quantity_available, inventory.quantity_reserved = 1, 100
    result = execute_tool(engine, account_id, conversation_id, "search_products", {"query": sku})
    assert result["items"][0]["price"] == price
    assert result["items"][0]["in_stock"] is True
    with seeded.begin() as db:
        db.get(Inventory, product_id).quantity_available = 0
    assert (
        execute_tool(engine, account_id, conversation_id, "search_products", {"query": sku})[
            "items"
        ][0]["in_stock"]
        is False
    )
    with seeded.begin() as db:
        db.get(Product, product_id).is_active = False
    assert (
        execute_tool(engine, account_id, conversation_id, "search_products", {"query": sku})[
            "total"
        ]
        == 0
    )
    assert (
        execute_tool(engine, account_id, conversation_id, "search_products", {"query": "%"})[
            "total"
        ]
        == 0
    )


def test_tool_inputs_are_bounded_and_mutations_are_absent(seeded):
    engine, account_id, conversation_id = context(seeded)
    for name, args in [
        ("list_orders", {"page": 0}),
        ("search_products", {"query": "x" * 101}),
        ("lookup_order", {"order_number": "x", "customer_id": str(uuid.uuid4())}),
    ]:
        assert (
            execute_tool(engine, account_id, conversation_id, name, args)["error"]
            == "invalid_arguments"
        )
    assert (
        execute_tool(engine, account_id, conversation_id, "cancel_order", {})["error"]
        == "unsupported_tool"
    )
