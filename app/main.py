from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import catalog, customers, orders
from app.config import settings
from app.db import engine

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(orders.router, prefix="/api/v1")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
def ready() -> dict[str, str]:
    with Session(engine) as session:
        session.execute(text("SELECT 1"))
    return {"status": "ready"}

