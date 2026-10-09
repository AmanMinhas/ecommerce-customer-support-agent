import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OrderStatus(str, enum.Enum):
    PLACED = "placed"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    REFUNDED = "refunded"


class ShipmentStatus(str, enum.Enum):
    PENDING = "pending"
    SHIPPED = "shipped"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"


def enum_type(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda x: [e.value for e in x])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    addresses: Mapped[list["Address"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Address(TimestampMixin, Base):
    __tablename__ = "addresses"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(20), default="shipping")
    line1: Mapped[str] = mapped_column(String(200))
    line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(100))
    postal_code: Mapped[str] = mapped_column(String(20))
    country: Mapped[str] = mapped_column(String(2), default="US", server_default="US")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    customer: Mapped[Customer] = relationship(back_populates="addresses")


class Category(TimestampMixin, Base):
    __tablename__ = "categories"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"), index=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    category: Mapped[Category | None] = relationship(back_populates="products")
    inventory: Mapped["Inventory"] = relationship(back_populates="product", cascade="all, delete-orphan", uselist=False)


class Inventory(Base):
    __tablename__ = "inventory"
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), primary_key=True)
    quantity_available: Mapped[int] = mapped_column(Integer, default=0)
    quantity_reserved: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    product: Mapped[Product] = relationship(back_populates="inventory")


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    status: Mapped[OrderStatus] = mapped_column(enum_type(OrderStatus, "order_status"), index=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    shipping_name: Mapped[str] = mapped_column(String(200))
    shipping_line1: Mapped[str] = mapped_column(String(200))
    shipping_line2: Mapped[str | None] = mapped_column(String(200))
    shipping_city: Mapped[str] = mapped_column(String(100))
    shipping_state: Mapped[str] = mapped_column(String(100))
    shipping_postal_code: Mapped[str] = mapped_column(String(20))
    shipping_country: Mapped[str] = mapped_column(String(2))
    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payments: Mapped[list["Payment"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    shipments: Mapped[list["Shipment"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(200))
    product_sku: Mapped[str] = mapped_column(String(64))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    order: Mapped[Order] = relationship(back_populates="items")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[OrderStatus | None] = mapped_column(enum_type(OrderStatus, "order_status"))
    to_status: Mapped[OrderStatus] = mapped_column(enum_type(OrderStatus, "order_status"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    order: Mapped[Order] = relationship(back_populates="status_history")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    status: Mapped[PaymentStatus] = mapped_column(enum_type(PaymentStatus, "payment_status"))
    method: Mapped[str] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    transaction_reference: Mapped[str | None] = mapped_column(String(100), unique=True)
    order: Mapped[Order] = relationship(back_populates="payments")


class Shipment(TimestampMixin, Base):
    __tablename__ = "shipments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    carrier: Mapped[str] = mapped_column(String(100))
    tracking_number: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[ShipmentStatus] = mapped_column(enum_type(ShipmentStatus, "shipment_status"))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order: Mapped[Order] = relationship(back_populates="shipments")


class Account(TimestampMixin, Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("email = lower(trim(email))", name="ck_accounts_normalized_email"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), unique=True
    )
    roles: Mapped[list["Role"]] = relationship(secondary="account_roles")


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    permissions: Mapped[list["Permission"]] = relationship(secondary="role_permissions")


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class AccountRole(Base):
    __tablename__ = "account_roles"
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True, index=True
    )
