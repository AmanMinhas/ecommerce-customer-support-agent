import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.common import get_or_404
from app.db import get_db
from app.models import Account
from app.permissions import PermissionCode
from app.schemas import Page
from app.security import require_permission

router = APIRouter(
    tags=["admin accounts"],
    dependencies=[
        Depends(require_permission(PermissionCode.ADMIN_ACCESS)),
        Depends(require_permission(PermissionCode.ACCOUNTS_MANAGE)),
    ],
)
DB = Annotated[Session, Depends(get_db)]


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    is_active: bool
    customer_id: uuid.UUID | None


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


@router.get("/accounts", response_model=Page)
def list_accounts(
    db: DB, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)
) -> Page:
    accounts = db.scalars(
        select(Account).order_by(Account.email).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page(
        items=[AccountRead.model_validate(a) for a in accounts],
        total=db.scalar(select(func.count()).select_from(Account)) or 0,
        page=page,
        page_size=page_size,
    )


@router.patch("/accounts/{account_id}", response_model=AccountRead)
def update_account(account_id: uuid.UUID, payload: AccountUpdate, db: DB) -> Account:
    account = get_or_404(db, Account, account_id)
    account.is_active = payload.is_active
    db.commit()
    return account
