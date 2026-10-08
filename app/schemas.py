import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import OrderStatus, PaymentStatus, ShipmentStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str | None = None


class CategoryRead(CategoryCreate, ORMModel):
    id: uuid.UUID


class InventoryRead(ORMModel):
    product_id: uuid.UUID
    quantity_available: int
    quantity_reserved: int


class InventoryUpdate(BaseModel):
    quantity_available: int = Field(ge=0)
    quantity_reserved: int | None = Field(default=None, ge=0)


class ProductCreate(BaseModel):
    category_id: uuid.UUID | None = None
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    price: Decimal = Field(gt=0, decimal_places=2)
    quantity_available: int = Field(default=0, ge=0)


class ProductUpdate(BaseModel):
    category_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    price: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    is_active: bool | None = None


class ProductRead(ORMModel):
    id: uuid.UUID
    category_id: uuid.UUID | None
    sku: str
    name: str
    description: str | None
    price: Decimal
    is_active: bool
    inventory: InventoryRead | None


class AddressBase(BaseModel):
    type: str = "shipping"
    line1: str
    line2: str | None = None
    city: str
    state: str
    postal_code: str
    country: str = Field(default="US", min_length=2, max_length=2)
    is_default: bool = False


class AddressCreate(AddressBase):
    pass


class AddressUpdate(BaseModel):
    type: str | None = None
    line1: str | None = None
    line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    is_default: bool | None = None


class AddressRead(AddressBase, ORMModel):
    id: uuid.UUID
    customer_id: uuid.UUID


class CustomerCreate(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    phone: str | None = None


class CustomerUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


class CustomerRead(CustomerCreate, ORMModel):
    id: uuid.UUID
    addresses: list[AddressRead] = []


class OrderItemCreate(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(gt=0, le=100)


class OrderCreate(BaseModel):
    customer_id: uuid.UUID
    shipping_address_id: uuid.UUID
    items: list[OrderItemCreate] = Field(min_length=1)


class OrderItemRead(ORMModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    product_sku: str
    unit_price: Decimal
    quantity: int
    line_total: Decimal


class StatusChange(BaseModel):
    status: OrderStatus
    note: str | None = None


class StatusHistoryRead(ORMModel):
    id: uuid.UUID
    from_status: OrderStatus | None
    to_status: OrderStatus
    note: str | None
    created_at: datetime


class PaymentCreate(BaseModel):
    method: str = "card"
    status: PaymentStatus = PaymentStatus.PAID
    transaction_reference: str | None = None


class PaymentRead(ORMModel):
    id: uuid.UUID
    order_id: uuid.UUID
    status: PaymentStatus
    method: str
    amount: Decimal
    transaction_reference: str | None


class ShipmentCreate(BaseModel):
    carrier: str
    tracking_number: str
    status: ShipmentStatus = ShipmentStatus.SHIPPED


class ShipmentUpdate(BaseModel):
    status: ShipmentStatus


class ShipmentRead(ORMModel):
    id: uuid.UUID
    order_id: uuid.UUID
    carrier: str
    tracking_number: str
    status: ShipmentStatus
    shipped_at: datetime | None
    delivered_at: datetime | None


class OrderRead(ORMModel):
    id: uuid.UUID
    order_number: str
    customer_id: uuid.UUID
    status: OrderStatus
    subtotal: Decimal
    shipping_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    shipping_name: str
    shipping_line1: str
    shipping_line2: str | None
    shipping_city: str
    shipping_state: str
    shipping_postal_code: str
    shipping_country: str
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemRead]
    payments: list[PaymentRead]
    shipments: list[ShipmentRead]


class Page(BaseModel):
    items: list
    total: int
    page: int
    page_size: int
