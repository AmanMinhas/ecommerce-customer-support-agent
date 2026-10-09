"""Create a deterministic, idempotent development dataset."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    Address,
    Category,
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
from app.scripts.seed_accounts import seed_accounts

CATEGORIES = [
    ("Electronics", "electronics"),
    ("Home", "home"),
    ("Books", "books"),
    ("Fashion", "fashion"),
    ("Sports", "sports"),
]
PRODUCT_NAMES = [
    "Wireless Headphones", "Mechanical Keyboard", "USB-C Hub", "Smart Watch", "Portable Speaker",
    "Desk Lamp", "Coffee Maker", "Cotton Bedsheet", "Water Bottle", "Storage Basket",
    "Python Handbook", "System Design Guide", "Database Essentials", "Clean Architecture", "FastAPI Field Guide",
    "Classic T-Shirt", "Running Shoes", "Canvas Backpack", "Winter Jacket", "Baseball Cap",
    "Yoga Mat", "Resistance Bands", "Cricket Bat", "Football", "Skipping Rope",
    "Webcam", "Laptop Stand", "Travel Mug", "Notebook Set", "Fitness Tracker",
]
US_ADDRESSES = [
    ("125 Market St", "San Francisco", "CA", "94105"),
    ("350 5th Ave", "New York", "NY", "10118"),
    ("233 S Wacker Dr", "Chicago", "IL", "60606"),
    ("600 Congress Ave", "Austin", "TX", "78701"),
    ("1201 3rd Ave", "Seattle", "WA", "98101"),
    ("200 Clarendon St", "Boston", "MA", "02116"),
    ("1801 California St", "Denver", "CO", "80202"),
    ("100 N Central Ave", "Phoenix", "AZ", "85004"),
    ("400 S Hope St", "Los Angeles", "CA", "90071"),
    ("201 S Biscayne Blvd", "Miami", "FL", "33131"),
]
STATUS_COUNTS = [
    (OrderStatus.PLACED, 3),
    (OrderStatus.CONFIRMED, 3),
    (OrderStatus.PROCESSING, 4),
    (OrderStatus.SHIPPED, 5),
    (OrderStatus.DELIVERED, 7),
    (OrderStatus.CANCELLED, 2),
    (OrderStatus.REFUNDED, 1),
]
PATHS = {
    OrderStatus.PLACED: [OrderStatus.PLACED],
    OrderStatus.CONFIRMED: [OrderStatus.PLACED, OrderStatus.CONFIRMED],
    OrderStatus.PROCESSING: [OrderStatus.PLACED, OrderStatus.CONFIRMED, OrderStatus.PROCESSING],
    OrderStatus.SHIPPED: [OrderStatus.PLACED, OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED],
    OrderStatus.DELIVERED: [OrderStatus.PLACED, OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED],
    OrderStatus.CANCELLED: [OrderStatus.PLACED, OrderStatus.CANCELLED],
    OrderStatus.REFUNDED: [OrderStatus.PLACED, OrderStatus.CONFIRMED, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.REFUNDED],
}


def seed() -> None:
    with SessionLocal.begin() as db:
        categories: list[Category] = []
        for name, slug in CATEGORIES:
            category = db.scalar(select(Category).where(Category.slug == slug))
            if not category:
                category = Category(name=name, slug=slug, description=f"{name} products")
                db.add(category)
                db.flush()
            categories.append(category)

        customers: list[Customer] = []
        addresses: list[Address] = []
        for index in range(1, 11):
            email = f"customer{index}@example.com"
            customer = db.scalar(select(Customer).where(Customer.email == email))
            if not customer:
                customer = Customer(
                    email=email,
                    first_name=f"Customer{index}",
                    last_name="Demo",
                    phone=f"+1-202-555-{1000 + index:04d}",
                )
                db.add(customer)
                db.flush()
            address = db.scalar(select(Address).where(Address.customer_id == customer.id))
            if not address:
                line1, city, state, postal_code = US_ADDRESSES[index - 1]
                address = Address(
                    customer_id=customer.id,
                    type="shipping",
                    line1=line1,
                    city=city,
                    state=state,
                    postal_code=postal_code,
                    country="US",
                    is_default=True,
                )
                db.add(address)
                db.flush()
            customers.append(customer)
            addresses.append(address)

        seed_accounts(db, customers)

        products: list[Product] = []
        for index, name in enumerate(PRODUCT_NAMES, start=1):
            sku = f"SKU-{index:04d}"
            product = db.scalar(select(Product).where(Product.sku == sku))
            if not product:
                product = Product(
                    category_id=categories[(index - 1) // 5 % len(categories)].id,
                    sku=sku,
                    name=name,
                    description=f"Demo {name.lower()}",
                    price=(Decimal("9.99") + Decimal(index * 5)).quantize(Decimal("0.01")),
                    is_active=True,
                )
                product.inventory = Inventory(quantity_available=50 + index, quantity_reserved=0)
                db.add(product)
                db.flush()
            products.append(product)

        statuses = [status for status, count in STATUS_COUNTS for _ in range(count)]
        now = datetime.now(UTC)
        for index, final_status in enumerate(statuses, start=1):
            order_number = f"ORD-{100000 + index}"
            if db.scalar(select(Order.id).where(Order.order_number == order_number)):
                continue
            customer = customers[(index - 1) % len(customers)]
            address = addresses[(index - 1) % len(addresses)]
            chosen = [products[(index * 2) % len(products)], products[(index * 2 + 1) % len(products)]]
            quantities = [1, 2]
            subtotal = sum((p.price * q for p, q in zip(chosen, quantities)), Decimal(0))
            shipping = Decimal("0.00") if subtotal >= 100 else Decimal("8.99")
            tax = (subtotal * Decimal("0.0825")).quantize(Decimal("0.01"))
            order = Order(
                order_number=order_number,
                customer_id=customer.id,
                status=final_status,
                subtotal=subtotal,
                shipping_amount=shipping,
                tax_amount=tax,
                total_amount=subtotal + shipping + tax,
                shipping_name=f"{customer.first_name} {customer.last_name}",
                shipping_line1=address.line1,
                shipping_line2=address.line2,
                shipping_city=address.city,
                shipping_state=address.state,
                shipping_postal_code=address.postal_code,
                shipping_country=address.country,
                created_at=now - timedelta(days=30 - index),
            )
            for product, quantity in zip(chosen, quantities):
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
            previous = None
            for step, state in enumerate(PATHS[final_status]):
                order.status_history.append(
                    OrderStatusHistory(
                        from_status=previous,
                        to_status=state,
                        note=f"Seeded transition to {state.value}",
                        created_at=order.created_at + timedelta(days=step),
                    )
                )
                previous = state
            payment_status = (
                PaymentStatus.REFUNDED if final_status == OrderStatus.REFUNDED
                else PaymentStatus.PENDING if final_status == OrderStatus.PLACED
                else PaymentStatus.PAID
            )
            order.payments.append(
                Payment(
                    status=payment_status,
                    method="card",
                    amount=order.total_amount,
                    transaction_reference=f"TXN-{100000 + index}",
                )
            )
            if final_status in {OrderStatus.SHIPPED, OrderStatus.DELIVERED, OrderStatus.REFUNDED}:
                delivered = final_status in {OrderStatus.DELIVERED, OrderStatus.REFUNDED}
                order.shipments.append(
                    Shipment(
                        carrier="Demo Express",
                        tracking_number=f"TRACK-{100000 + index}",
                        status=ShipmentStatus.DELIVERED if delivered else ShipmentStatus.IN_TRANSIT,
                        shipped_at=order.created_at + timedelta(days=3),
                        delivered_at=order.created_at + timedelta(days=7) if delivered else None,
                    )
                )
            db.add(order)

    print("Seed complete: 10 customer accounts, 2 admins, 30 products, and 25 lifecycle-varied orders ensured.")


if __name__ == "__main__":
    seed()
