import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.common import get_or_404
from app.db import get_db
from app.models import (
    Address,
    Customer,
    Inventory,
    Order,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
    Payment,
    PaymentStatus,
    Product,
    Shipment,
    ShipmentStatus,
)
from app.schemas import (
    OrderCreate,
    OrderRead,
    Page,
    PaymentCreate,
    PaymentRead,
    ShipmentCreate,
    ShipmentRead,
    ShipmentUpdate,
    StatusChange,
    StatusHistoryRead,
)

router = APIRouter(tags=["orders"])

ALLOWED_TRANSITIONS = {
    OrderStatus.PLACED: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PROCESSING, OrderStatus.CANCELLED},
    OrderStatus.PROCESSING: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


def load_order(db: Session, clause) -> Order:
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.shipments),
        )
        .where(clause)
    )
    if not order:
        raise HTTPException(404, "Order not found")
    return order


def next_order_number(db: Session) -> str:
    # UUID-derived suffix avoids a fragile max()+1 sequence under concurrent requests.
    while True:
        candidate = f"ORD-{uuid.uuid4().hex[:10].upper()}"
        if not db.scalar(select(Order.id).where(Order.order_number == candidate)):
            return candidate


@router.post("/orders", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    customer = get_or_404(db, Customer, payload.customer_id)
    address = db.scalar(
        select(Address).where(
            Address.id == payload.shipping_address_id,
            Address.customer_id == payload.customer_id,
        )
    )
    if not address:
        raise HTTPException(400, "Shipping address does not belong to customer")

    requested: dict[uuid.UUID, int] = {}
    for item in payload.items:
        requested[item.product_id] = requested.get(item.product_id, 0) + item.quantity

    product_rows = db.execute(
        select(Product, Inventory)
        .join(Inventory)
        .where(Product.id.in_(requested), Product.is_active.is_(True))
        .with_for_update(of=Inventory)
    ).all()
    products = {product.id: (product, inventory) for product, inventory in product_rows}
    missing = set(requested) - set(products)
    if missing:
        raise HTTPException(400, f"Unavailable products: {', '.join(map(str, missing))}")
    for product_id, quantity in requested.items():
        if products[product_id][1].quantity_available < quantity:
            raise HTTPException(409, f"Insufficient inventory for SKU {products[product_id][0].sku}")

    subtotal = sum(
        (products[product_id][0].price * quantity for product_id, quantity in requested.items()),
        Decimal("0.00"),
    )
    shipping_amount = Decimal("0.00") if subtotal >= Decimal("100.00") else Decimal("8.99")
    # Simplified demo rate. Production US tax must be calculated from the delivery jurisdiction.
    tax_amount = (subtotal * Decimal("0.0825")).quantize(Decimal("0.01"))
    order = Order(
        order_number=next_order_number(db),
        customer_id=customer.id,
        status=OrderStatus.PLACED,
        subtotal=subtotal,
        shipping_amount=shipping_amount,
        tax_amount=tax_amount,
        total_amount=subtotal + shipping_amount + tax_amount,
        shipping_name=f"{customer.first_name} {customer.last_name}",
        shipping_line1=address.line1,
        shipping_line2=address.line2,
        shipping_city=address.city,
        shipping_state=address.state,
        shipping_postal_code=address.postal_code,
        shipping_country=address.country,
    )
    for product_id, quantity in requested.items():
        product, inventory = products[product_id]
        inventory.quantity_available -= quantity
        inventory.quantity_reserved += quantity
        order.items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                product_sku=product.sku,
                unit_price=product.price,
                quantity=quantity,
                line_total=product.price * quantity,
            )
        )
    order.status_history.append(
        OrderStatusHistory(from_status=None, to_status=OrderStatus.PLACED, note="Order placed")
    )
    db.add(order)
    db.commit()
    return load_order(db, Order.id == order.id)


@router.get("/orders", response_model=Page)
def list_orders(
    customer_id: uuid.UUID | None = None,
    order_status: OrderStatus | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(Order).options(
        selectinload(Order.items), selectinload(Order.payments), selectinload(Order.shipments)
    )
    if customer_id:
        stmt = stmt.where(Order.customer_id == customer_id)
    if order_status:
        stmt = stmt.where(Order.status == order_status)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    orders = db.scalars(
        stmt.order_by(Order.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page(items=[OrderRead.model_validate(x) for x in orders], total=total, page=page, page_size=page_size)


@router.get("/orders/number/{order_number}", response_model=OrderRead)
def get_order_by_number(order_number: str, db: Session = Depends(get_db)):
    return load_order(db, Order.order_number == order_number)


@router.get("/orders/{order_id}", response_model=OrderRead)
def get_order(order_id: uuid.UUID, db: Session = Depends(get_db)):
    return load_order(db, Order.id == order_id)


@router.patch("/orders/{order_id}/status", response_model=OrderRead)
def change_status(order_id: uuid.UUID, payload: StatusChange, db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order:
        raise HTTPException(404, "Order not found")
    if payload.status not in ALLOWED_TRANSITIONS[order.status]:
        raise HTTPException(409, f"Cannot transition from {order.status.value} to {payload.status.value}")
    previous = order.status
    order.status = payload.status
    if payload.status == OrderStatus.SHIPPED:
        for item in order.items:
            inventory = get_or_404(db, Inventory, item.product_id)
            inventory.quantity_reserved = max(0, inventory.quantity_reserved - item.quantity)
    order.status_history.append(
        OrderStatusHistory(from_status=previous, to_status=payload.status, note=payload.note)
    )
    db.commit()
    return load_order(db, Order.id == order_id)


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
def cancel_order(order_id: uuid.UUID, db: Session = Depends(get_db)):
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if not order:
        raise HTTPException(404, "Order not found")
    if OrderStatus.CANCELLED not in ALLOWED_TRANSITIONS[order.status]:
        raise HTTPException(409, f"Order in {order.status.value} state cannot be cancelled")
    for item in order.items:
        inventory = get_or_404(db, Inventory, item.product_id)
        inventory.quantity_available += item.quantity
        inventory.quantity_reserved = max(0, inventory.quantity_reserved - item.quantity)
    previous = order.status
    order.status = OrderStatus.CANCELLED
    order.status_history.append(
        OrderStatusHistory(from_status=previous, to_status=OrderStatus.CANCELLED, note="Cancelled")
    )
    db.commit()
    return load_order(db, Order.id == order_id)


@router.get("/orders/{order_id}/status-history", response_model=list[StatusHistoryRead])
def get_status_history(order_id: uuid.UUID, db: Session = Depends(get_db)):
    get_or_404(db, Order, order_id)
    return db.scalars(
        select(OrderStatusHistory)
        .where(OrderStatusHistory.order_id == order_id)
        .order_by(OrderStatusHistory.created_at)
    ).all()


@router.get("/orders/{order_id}/payments", response_model=list[PaymentRead])
def get_payments(order_id: uuid.UUID, db: Session = Depends(get_db)):
    get_or_404(db, Order, order_id)
    return db.scalars(select(Payment).where(Payment.order_id == order_id)).all()


@router.post("/orders/{order_id}/payments", response_model=PaymentRead, status_code=201)
def create_payment(order_id: uuid.UUID, payload: PaymentCreate, db: Session = Depends(get_db)):
    order = get_or_404(db, Order, order_id)
    payment = Payment(order_id=order.id, amount=order.total_amount, **payload.model_dump())
    db.add(payment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Transaction reference already exists") from exc
    return payment


@router.get("/orders/{order_id}/shipments", response_model=list[ShipmentRead])
def get_shipments(order_id: uuid.UUID, db: Session = Depends(get_db)):
    get_or_404(db, Order, order_id)
    return db.scalars(select(Shipment).where(Shipment.order_id == order_id)).all()


@router.post("/orders/{order_id}/shipments", response_model=ShipmentRead, status_code=201)
def create_shipment(order_id: uuid.UUID, payload: ShipmentCreate, db: Session = Depends(get_db)):
    get_or_404(db, Order, order_id)
    shipment = Shipment(
        order_id=order_id,
        **payload.model_dump(),
        shipped_at=datetime.now(UTC) if payload.status != ShipmentStatus.PENDING else None,
    )
    db.add(shipment)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Tracking number already exists") from exc
    return shipment


@router.patch("/shipments/{shipment_id}", response_model=ShipmentRead)
def update_shipment(shipment_id: uuid.UUID, payload: ShipmentUpdate, db: Session = Depends(get_db)):
    shipment = get_or_404(db, Shipment, shipment_id)
    shipment.status = payload.status
    now = datetime.now(UTC)
    if payload.status in {ShipmentStatus.SHIPPED, ShipmentStatus.IN_TRANSIT} and not shipment.shipped_at:
        shipment.shipped_at = now
    if payload.status == ShipmentStatus.DELIVERED:
        shipment.delivered_at = now
    db.commit()
    return shipment
