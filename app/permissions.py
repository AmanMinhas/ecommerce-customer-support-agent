"""Stable capability identifiers; assignments remain managed in the database."""

from enum import StrEnum


class PermissionCode(StrEnum):
    USER_ACCESS = "user:access"
    ADMIN_ACCESS = "admin:access"
    PROFILE_READ_OWN = "profile:read_own"
    PROFILE_WRITE_OWN = "profile:write_own"
    ORDERS_CREATE_OWN = "orders:create_own"
    ORDERS_READ_OWN = "orders:read_own"
    ORDERS_CANCEL_OWN = "orders:cancel_own"
    ORDERS_READ_ALL = "orders:read_all"
    ORDERS_MANAGE = "orders:manage"
    CUSTOMERS_MANAGE = "customers:manage"
    CATALOG_READ_ALL = "catalog:read_all"
    CATALOG_WRITE = "catalog:write"
    INVENTORY_MANAGE = "inventory:manage"
    ACCOUNTS_MANAGE = "accounts:manage"


class BuiltinRole(StrEnum):
    """Default seed roles only; custom database role names are unrestricted."""

    USER = "user"
    ADMIN = "admin"
