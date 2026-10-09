import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.common import apply_updates, get_or_404
from app.db import get_db
from app.models import Address, Customer
from app.permissions import PermissionCode
from app.schemas import (
    AddressCreate,
    AddressRead,
    AddressUpdate,
    CustomerCreate,
    CustomerRead,
    CustomerUpdate,
    Page,
)
from app.security import require_permission

router = APIRouter(tags=["admin customers"], dependencies=[Depends(require_permission(PermissionCode.ADMIN_ACCESS)), Depends(require_permission(PermissionCode.CUSTOMERS_MANAGE))])


@router.post("/customers", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    customer = Customer(**payload.model_dump())
    db.add(customer)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Email already exists") from exc
    return customer


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(customer_id: uuid.UUID, db: Session = Depends(get_db)):
    customer = db.scalar(
        select(Customer).options(selectinload(Customer.addresses)).where(Customer.id == customer_id)
    )
    if not customer:
        raise HTTPException(404, "Customer not found")
    return customer


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(customer_id: uuid.UUID, payload: CustomerUpdate, db: Session = Depends(get_db)):
    customer = get_or_404(db, Customer, customer_id)
    apply_updates(customer, payload.model_dump(exclude_unset=True))
    db.commit()
    return customer


@router.get("/customers/{customer_id}/addresses", response_model=list[AddressRead])
def list_addresses(customer_id: uuid.UUID, db: Session = Depends(get_db)):
    get_or_404(db, Customer, customer_id)
    return db.scalars(select(Address).where(Address.customer_id == customer_id)).all()


@router.post(
    "/customers/{customer_id}/addresses",
    response_model=AddressRead,
    status_code=status.HTTP_201_CREATED,
)
def create_address(customer_id: uuid.UUID, payload: AddressCreate, db: Session = Depends(get_db)):
    get_or_404(db, Customer, customer_id)
    if payload.is_default:
        db.execute(update(Address).where(Address.customer_id == customer_id).values(is_default=False))
    address = Address(customer_id=customer_id, **payload.model_dump())
    db.add(address)
    db.commit()
    return address


@router.patch("/addresses/{address_id}", response_model=AddressRead)
def update_address(address_id: uuid.UUID, payload: AddressUpdate, db: Session = Depends(get_db)):
    address = get_or_404(db, Address, address_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("is_default"):
        db.execute(
            update(Address)
            .where(Address.customer_id == address.customer_id, Address.id != address.id)
            .values(is_default=False)
        )
    apply_updates(address, values)
    db.commit()
    return address


@router.delete("/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(address_id: uuid.UUID, db: Session = Depends(get_db)):
    address = get_or_404(db, Address, address_id)
    db.delete(address)
    db.commit()



@router.get("/customers", response_model=Page)
def list_customers(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   db: Session = Depends(get_db)) -> Page:
    customers = db.scalars(select(Customer).options(selectinload(Customer.addresses))
                           .order_by(Customer.email).offset((page - 1) * page_size)
                           .limit(page_size)).all()
    return Page(items=[CustomerRead.model_validate(c) for c in customers],
                total=db.scalar(select(func.count()).select_from(Customer)) or 0,
                page=page, page_size=page_size)
