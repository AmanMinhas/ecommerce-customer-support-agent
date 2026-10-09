"""Password, token, and database permission boundaries."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.models import Account, Role
from app.permissions import PermissionCode

password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)
# Keep unknown-email and known-email failures on the same password verification path.
DUMMY_HASH = password_hasher.hash("dummy-password-never-used-for-login")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def issue_token(account: Account) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(account.id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )


def permissions_for(account: Account) -> set[str]:
    return {permission.name for role in account.roles for permission in role.permissions}


def account_query() -> Select[tuple[Account]]:
    return select(Account).options(selectinload(Account.roles).selectinload(Role.permissions))


def unauthorized() -> HTTPException:
    return HTTPException(
        401, "Invalid credentials or token", headers={"WWW-Authenticate": "Bearer"}
    )


def current_account(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Account:
    if credentials is None:
        raise unauthorized()
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "iat", "exp", "iss", "aud"]},
        )
        account_id = uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise unauthorized() from None
    account = db.scalar(account_query().where(Account.id == account_id))
    if account is None or not account.is_active:
        raise unauthorized()
    return account


def require_permission(permission: PermissionCode) -> Callable[[Account], Account]:
    def dependency(account: Account = Depends(current_account)) -> Account:
        if permission.value not in permissions_for(account):
            raise HTTPException(403, "Insufficient permissions")
        return account

    return dependency


def customer_account(permission: PermissionCode) -> Callable[[Account], Account]:
    def dependency(account: Account = Depends(require_permission(permission))) -> Account:
        if account.customer_id is None:
            raise HTTPException(403, "A customer profile is required")
        return account

    return dependency
