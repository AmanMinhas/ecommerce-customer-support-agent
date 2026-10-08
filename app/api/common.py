import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session


def get_or_404(db: Session, model: type, object_id: uuid.UUID):
    instance = db.get(model, object_id)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} not found")
    return instance


def apply_updates(instance, values: dict) -> None:
    for key, value in values.items():
        setattr(instance, key, value)

