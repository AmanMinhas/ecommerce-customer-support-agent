"""Shared eager-loaded commerce reads for HTTP handlers and support tools."""

from sqlalchemy import Select, select
from sqlalchemy.orm import selectinload

from app.models import Order, Product


def order_query() -> Select[tuple[Order]]:
    return select(Order).options(
        selectinload(Order.items), selectinload(Order.payments), selectinload(Order.shipments)
    )


def active_product_query() -> Select[tuple[Product]]:
    return (
        select(Product).options(selectinload(Product.inventory)).where(Product.is_active.is_(True))
    )
