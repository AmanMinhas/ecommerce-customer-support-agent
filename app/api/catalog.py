import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.common import apply_updates, get_or_404
from app.db import get_db
from app.models import Category, Inventory, Product
from app.permissions import PermissionCode
from app.schemas import (
    CategoryCreate,
    CategoryRead,
    InventoryRead,
    InventoryUpdate,
    Page,
    ProductCreate,
    ProductRead,
    ProductUpdate,
)
from app.security import require_permission

router = APIRouter(tags=["catalog"])
admin_router = APIRouter(tags=["admin catalog"], dependencies=[Depends(require_permission(PermissionCode.ADMIN_ACCESS))])


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(db: Session = Depends(get_db)):
    return db.scalars(select(Category).order_by(Category.name)).all()


@admin_router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission(PermissionCode.CATALOG_WRITE))])
def create_category(payload: CategoryCreate, db: Session = Depends(get_db)):
    category = Category(**payload.model_dump())
    db.add(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Category name or slug already exists") from exc
    db.refresh(category)
    return category


@router.get("/products", response_model=Page)
def list_products(
    category: str | None = None,
    search: str | None = None,
    in_stock: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    stmt = select(Product).options(selectinload(Product.inventory)).where(Product.is_active.is_(True))
    if category:
        stmt = stmt.join(Category).where(Category.slug == category)
    if search:
        stmt = stmt.where(or_(Product.name.ilike(f"%{search}%"), Product.sku.ilike(f"%{search}%")))
    if in_stock is True:
        stmt = stmt.join(Inventory).where(Inventory.quantity_available > 0)
    elif in_stock is False:
        stmt = stmt.outerjoin(Inventory).where(
            or_(Inventory.product_id.is_(None), Inventory.quantity_available <= 0)
        )
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    products = db.scalars(stmt.order_by(Product.name).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[ProductRead.model_validate(x) for x in products], total=total, page=page, page_size=page_size)


@router.get("/products/{product_id}", response_model=ProductRead)
def get_product(product_id: uuid.UUID, db: Session = Depends(get_db)):
    product = db.scalar(select(Product).options(selectinload(Product.inventory)).where(Product.id == product_id, Product.is_active.is_(True)))
    if not product:
        raise HTTPException(404, "Product not found")
    return product


@admin_router.post("/products", response_model=ProductRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_permission(PermissionCode.CATALOG_WRITE))])
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    values = payload.model_dump(exclude={"quantity_available"})
    if values["category_id"] and not db.get(Category, values["category_id"]):
        raise HTTPException(404, "Category not found")
    product = Product(**values)
    product.inventory = Inventory(quantity_available=payload.quantity_available)
    db.add(product)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "SKU already exists") from exc
    return product


@admin_router.patch("/products/{product_id}", response_model=ProductRead, dependencies=[Depends(require_permission(PermissionCode.CATALOG_WRITE))])
def update_product(product_id: uuid.UUID, payload: ProductUpdate, db: Session = Depends(get_db)):
    product = get_or_404(db, Product, product_id)
    values = payload.model_dump(exclude_unset=True)
    if values.get("category_id") and not db.get(Category, values["category_id"]):
        raise HTTPException(404, "Category not found")
    apply_updates(product, values)
    db.commit()
    return product


@admin_router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_permission(PermissionCode.CATALOG_WRITE))])
def deactivate_product(product_id: uuid.UUID, db: Session = Depends(get_db)):
    product = get_or_404(db, Product, product_id)
    product.is_active = False
    db.commit()


@admin_router.get("/products/{product_id}/inventory", response_model=InventoryRead, dependencies=[Depends(require_permission(PermissionCode.INVENTORY_MANAGE))])
def get_inventory(product_id: uuid.UUID, db: Session = Depends(get_db)):
    get_or_404(db, Product, product_id)
    return get_or_404(db, Inventory, product_id)


@admin_router.patch("/products/{product_id}/inventory", response_model=InventoryRead, dependencies=[Depends(require_permission(PermissionCode.INVENTORY_MANAGE))])
def update_inventory(product_id: uuid.UUID, payload: InventoryUpdate, db: Session = Depends(get_db)):
    inventory = get_or_404(db, Inventory, product_id)
    apply_updates(inventory, payload.model_dump(exclude_unset=True))
    db.commit()
    return inventory


@admin_router.get("/products", response_model=Page,
                  dependencies=[Depends(require_permission(PermissionCode.CATALOG_READ_ALL))])
def admin_list_products(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                        db: Session = Depends(get_db)) -> Page:
    products = db.scalars(select(Product).options(selectinload(Product.inventory))
                          .order_by(Product.name, Product.id).offset((page - 1) * page_size)
                          .limit(page_size)).all()
    return Page(items=[ProductRead.model_validate(p) for p in products],
                total=db.scalar(select(func.count()).select_from(Product)) or 0,
                page=page, page_size=page_size)


@admin_router.get("/products/{product_id}", response_model=ProductRead,
                  dependencies=[Depends(require_permission(PermissionCode.CATALOG_READ_ALL))])
def admin_get_product(product_id: uuid.UUID, db: Session = Depends(get_db)) -> Product:
    return get_or_404(db, Product, product_id)


@admin_router.get("/categories", response_model=list[CategoryRead],
                  dependencies=[Depends(require_permission(PermissionCode.CATALOG_READ_ALL))])
def admin_list_categories(db: Session = Depends(get_db)) -> list[Category]:
    return list_categories(db)
