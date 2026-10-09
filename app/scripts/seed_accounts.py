"""Initial demo grants. Runtime authorization reads the database, not this mapping."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, Customer, Permission, Role
from app.permissions import BuiltinRole, PermissionCode
from app.security import hash_password

SEED_PASSWORD = "DemoPass123!"
DEFAULT_ROLES: dict[BuiltinRole, set[PermissionCode]] = {
    BuiltinRole.USER: {
        PermissionCode.USER_ACCESS,
        PermissionCode.PROFILE_READ_OWN,
        PermissionCode.PROFILE_WRITE_OWN,
        PermissionCode.ORDERS_CREATE_OWN,
        PermissionCode.ORDERS_READ_OWN,
        PermissionCode.ORDERS_CANCEL_OWN,
    },
    BuiltinRole.ADMIN: {
        PermissionCode.ADMIN_ACCESS,
        PermissionCode.CUSTOMERS_MANAGE,
        PermissionCode.CATALOG_READ_ALL,
        PermissionCode.CATALOG_WRITE,
        PermissionCode.INVENTORY_MANAGE,
        PermissionCode.ORDERS_READ_ALL,
        PermissionCode.ORDERS_MANAGE,
        PermissionCode.ACCOUNTS_MANAGE,
    },
}


def seed_accounts(db: Session, customers: list[Customer]) -> None:
    permissions: dict[PermissionCode, Permission] = {}
    for name in sorted(set.union(*DEFAULT_ROLES.values())):
        permission = db.scalar(select(Permission).where(Permission.name == name.value))
        if permission is None:
            permission = Permission(name=name.value)
            db.add(permission)
            db.flush()
        permissions[name] = permission
    roles: dict[BuiltinRole, Role] = {}
    for name, grants in DEFAULT_ROLES.items():
        role = db.scalar(select(Role).where(Role.name == name.value))
        if role is None:
            role = Role(name=name.value, permissions=[permissions[key] for key in sorted(grants)])
            db.add(role)
            db.flush()
        roles[name] = role
    entries = [(customer.email, customer.id, BuiltinRole.USER) for customer in customers]
    entries += [(f"admin{index}@example.com", None, BuiltinRole.ADMIN) for index in (1, 2)]
    for email, customer_id, role in entries:
        if db.scalar(select(Account).where(Account.email == email.lower())) is None:
            db.add(
                Account(
                    email=email.lower(),
                    customer_id=customer_id,
                    password_hash=hash_password(SEED_PASSWORD),
                    roles=[roles[role]],
                )
            )
            db.flush()
