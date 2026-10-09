"""Customer API: permissions and ownership are checked before shared operations."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import customers, orders
from app.db import get_db
from app.models import Account, Address, Order, OrderStatus
from app.permissions import PermissionCode
from app.schemas import (
    AddressCreate,
    AddressRead,
    AddressUpdate,
    CustomerRead,
    CustomerUpdate,
    OrderCreate,
    OrderItemCreate,
    OrderRead,
    Page,
    PaymentRead,
    ShipmentRead,
    StatusHistoryRead,
)
from app.security import customer_account, require_permission

router = APIRouter(
    tags=["customer self service"], dependencies=[Depends(require_permission(PermissionCode.USER_ACCESS))]
)
DB = Annotated[Session, Depends(get_db)]
ProfileReader = Annotated[Account, Depends(customer_account(PermissionCode.PROFILE_READ_OWN))]
ProfileWriter = Annotated[Account, Depends(customer_account(PermissionCode.PROFILE_WRITE_OWN))]
OrderReader = Annotated[Account, Depends(customer_account(PermissionCode.ORDERS_READ_OWN))]
OrderWriter = Annotated[Account, Depends(customer_account(PermissionCode.ORDERS_CREATE_OWN))]
OrderCanceller = Annotated[Account, Depends(customer_account(PermissionCode.ORDERS_CANCEL_OWN))]


class OwnOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shipping_address_id: uuid.UUID
    items: list[OrderItemCreate] = Field(min_length=1)


def owned_order(db: Session, account: Account, order_id: uuid.UUID) -> Order:
    order = db.scalar(
        select(Order).where(Order.id == order_id, Order.customer_id == account.customer_id)
    )
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


def owned_address(db: Session, account: Account, address_id: uuid.UUID) -> Address:
    address = db.scalar(
        select(Address).where(Address.id == address_id, Address.customer_id == account.customer_id)
    )
    if address is None:
        raise HTTPException(404, "Address not found")
    return address


@router.get("/customers/me", response_model=CustomerRead)
def profile(account: ProfileReader, db: DB) -> CustomerRead:
    return customers.get_customer(account.customer_id, db)


@router.patch("/customers/me", response_model=CustomerRead)
def update_profile(payload: CustomerUpdate, account: ProfileWriter, db: DB) -> CustomerRead:
    return customers.update_customer(account.customer_id, payload, db)


@router.get("/customers/me/addresses", response_model=list[AddressRead])
def addresses(account: ProfileReader, db: DB) -> list[AddressRead]:
    return customers.list_addresses(account.customer_id, db)


@router.post("/customers/me/addresses", response_model=AddressRead, status_code=201)
def add_address(payload: AddressCreate, account: ProfileWriter, db: DB) -> AddressRead:
    return customers.create_address(account.customer_id, payload, db)


@router.patch("/addresses/{address_id}", response_model=AddressRead)
def edit_address(
    address_id: uuid.UUID, payload: AddressUpdate, account: ProfileWriter, db: DB
) -> AddressRead:
    owned_address(db, account, address_id)
    return customers.update_address(address_id, payload, db)


@router.delete("/addresses/{address_id}", status_code=204)
def remove_address(address_id: uuid.UUID, account: ProfileWriter, db: DB) -> None:
    owned_address(db, account, address_id)
    customers.delete_address(address_id, db)


@router.post("/orders", response_model=OrderRead, status_code=201)
def create_order(payload: OwnOrderCreate, account: OrderWriter, db: DB) -> OrderRead:
    return orders.create_order(
        OrderCreate(customer_id=account.customer_id, **payload.model_dump()), db
    )


@router.get("/orders", response_model=Page)
def list_orders(
    account: OrderReader,
    db: DB,
    order_status: OrderStatus | None = Query(default=None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> Page:
    return orders.list_orders(account.customer_id, order_status, page, page_size, db)


@router.get("/orders/number/{order_number}", response_model=OrderRead)
def by_number(order_number: str, account: OrderReader, db: DB) -> Order:
    return orders.load_order(
        db, (Order.order_number == order_number) & (Order.customer_id == account.customer_id)
    )


@router.get("/orders/{order_id}", response_model=OrderRead)
def get_order(order_id: uuid.UUID, account: OrderReader, db: DB) -> OrderRead:
    owned_order(db, account, order_id)
    return orders.get_order(order_id, db)


@router.post("/orders/{order_id}/cancel", response_model=OrderRead)
def cancel_order(order_id: uuid.UUID, account: OrderCanceller, db: DB) -> OrderRead:
    owned_order(db, account, order_id)
    return orders.cancel_order(order_id, db)


@router.get("/orders/{order_id}/status-history", response_model=list[StatusHistoryRead])
def history(order_id: uuid.UUID, account: OrderReader, db: DB) -> list[StatusHistoryRead]:
    owned_order(db, account, order_id)
    return orders.get_status_history(order_id, db)


@router.get("/orders/{order_id}/payments", response_model=list[PaymentRead])
def payments(order_id: uuid.UUID, account: OrderReader, db: DB) -> list[PaymentRead]:
    owned_order(db, account, order_id)
    return orders.get_payments(order_id, db)


@router.get("/orders/{order_id}/shipments", response_model=list[ShipmentRead])
def shipments(order_id: uuid.UUID, account: OrderReader, db: DB) -> list[ShipmentRead]:
    owned_order(db, account, order_id)
    return orders.get_shipments(order_id, db)
