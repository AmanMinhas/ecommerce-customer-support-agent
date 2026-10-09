"""Tests require a disposable PostgreSQL cluster, never the application database."""

import getpass
import os
import uuid

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"postgresql+psycopg://{getpass.getuser()}@127.0.0.1:55439/postgres"
)
os.environ["JWT_SECRET"] = "test-only-signing-secret-with-at-least-32-characters"

from app.db import get_db
from app.main import app


@pytest.fixture()
def database():
    # A fresh schema per test isolates migration, seed, and API writes.
    engine = create_engine(os.environ["DATABASE_URL"])
    schema = "test_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    isolated = create_engine(
        os.environ["DATABASE_URL"], connect_args={"options": f"-csearch_path={schema}"}
    )
    from app.config import settings

    original_url = settings.database_url
    try:
        settings.database_url = original_url + f"?options=-csearch_path={schema}"
        try:
            command.upgrade(Config("alembic.ini"), "head")
        finally:
            settings.database_url = original_url
        yield sessionmaker(isolated, expire_on_commit=False)
    finally:
        isolated.dispose()
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()


@pytest.fixture()
def client(database):
    def override_db():
        with database() as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def seeded(database, monkeypatch):
    from app.scripts import seed

    monkeypatch.setattr(seed, "SessionLocal", database)
    seed.seed()
    return database
