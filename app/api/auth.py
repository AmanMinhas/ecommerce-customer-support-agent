from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Account
from app.permissions import PermissionCode
from app.security import (
    DUMMY_HASH,
    account_query,
    current_account,
    issue_token,
    permissions_for,
    unauthorized,
    verify_password,
)

router = APIRouter(tags=["authentication"])


class Login(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower() if isinstance(value, str) else value


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def authenticate(payload: Login, db: Session, permission: PermissionCode) -> Token:
    account = db.scalar(account_query().where(Account.email == str(payload.email)))
    valid = verify_password(payload.password, account.password_hash if account else DUMMY_HASH)
    if not valid or account is None or not account.is_active:
        raise unauthorized()
    if permission.value not in permissions_for(account):
        raise HTTPException(403, "Insufficient permissions")
    return Token(access_token=issue_token(account), expires_in=settings.jwt_expiry_minutes * 60)


@router.post("/auth/login", response_model=Token)
def user_login(payload: Login, db: Session = Depends(get_db)) -> Token:
    return authenticate(payload, db, PermissionCode.USER_ACCESS)


@router.post("/admin/auth/login", response_model=Token)
def admin_login(payload: Login, db: Session = Depends(get_db)) -> Token:
    return authenticate(payload, db, PermissionCode.ADMIN_ACCESS)


@router.get("/auth/me")
def me(account: Account = Depends(current_account)) -> dict:
    return {
        "id": account.id,
        "email": account.email,
        "customer_id": account.customer_id,
        "roles": sorted(role.name for role in account.roles),
        "permissions": sorted(permissions_for(account)),
    }
