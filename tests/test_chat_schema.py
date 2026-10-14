import uuid

import pytest
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from test_authorization import headers
from test_chat import BASE, create_conversation

from alembic import command
from app.config import settings
from app.models import ChatConversation, ChatMessage


def test_message_constraints_and_chat_migration_rollback(client, seeded):
    auth = headers(client)
    conversation = create_conversation(client, auth).json()
    cid = uuid.UUID(conversation["id"])
    client.post(f"{BASE}/{cid}/handoff", headers=auth)
    with seeded() as db:
        original = db.scalar(select(ChatMessage).where(ChatMessage.conversation_id == cid))
        sequence = original.sequence
    for values in [
        {"sequence": sequence, "content": "duplicate"},
        {"sequence": 2, "content": ""},
        {"sequence": -1, "content": "invalid"},
    ]:
        with seeded() as db, pytest.raises(IntegrityError):
            db.add(ChatMessage(conversation_id=cid, author="customer", **values))
            db.commit()
    with seeded() as db:
        schema = db.scalar(text("SELECT current_schema()"))
    original_url = settings.database_url
    try:
        settings.database_url = original_url + f"?options=-csearch_path={schema}"
        command.downgrade(Config("alembic.ini"), "0002")
        with seeded() as db:
            assert "chat_conversations" not in inspect(db.bind).get_table_names()
            assert db.scalar(text("SELECT count(*) FROM accounts")) == 12
            assert db.scalar(text("SELECT count(*) FROM orders")) == 25
        command.upgrade(Config("alembic.ini"), "head")
        with seeded() as db:
            assert db.scalar(select(ChatConversation.id)) is None
            assert (
                db.scalar(
                    text(
                        "SELECT count(*) FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE p.name IN ('chat:own','chat:manage')"
                    )
                )
                == 2
            )
    finally:
        settings.database_url = original_url
