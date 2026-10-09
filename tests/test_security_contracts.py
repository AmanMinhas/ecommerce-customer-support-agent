import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from alembic.config import Config
from argon2 import PasswordHasher
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from test_auth import login
from test_authorization import headers, ids

from alembic import command
from app.config import Settings, settings
from app.models import Account, AccountRole, Permission, Role, RolePermission


def test_token_validation(client, seeded):
    auth = headers(client)
    token = auth["Authorization"].split()[1]
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=["HS256"],
        audience=settings.jwt_audience,
        issuer=settings.jwt_issuer,
    )
    bad_claims = [
        {**claims, "exp": datetime.now(UTC) - timedelta(seconds=1)},
        {**claims, "aud": "another-app"},
        {**claims, "iss": "another-issuer"},
        {**claims, "sub": "invalid-uuid"},
        {**claims, "sub": str(uuid.uuid4())},
        {k: v for k, v in claims.items() if k != "exp"},
    ]
    tokens = [
        "malformed",
        jwt.encode(claims, "different-key-that-is-at-least-32-bytes", algorithm="HS256"),
    ]
    tokens += [
        jwt.encode(c, settings.jwt_secret.get_secret_value(), algorithm="HS256") for c in bad_claims
    ]
    for token in tokens:
        result = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + token})
        assert result.status_code == 401
        assert result.headers["www-authenticate"] == "Bearer"


def test_multiple_roles_and_custom_role_without_hardcoded_names(client, seeded):
    user_token = headers(client)
    with seeded.begin() as db:
        user = db.scalar(select(Account).where(Account.email == "customer1@example.com"))
        grants = db.scalars(
            select(Permission).where(Permission.name.in_(["admin:access", "orders:read_all"]))
        ).all()
        user.roles.append(Role(name="support_agent", permissions=list(grants)))
    assert login(client, admin=True).status_code == 200
    assert client.get("/api/v1/admin/orders", headers=user_token).json()["total"] == 25
    assert client.get("/api/v1/orders", headers=user_token).json()["total"] == 3
    assert client.get("/api/v1/admin/accounts", headers=user_token).status_code == 403
    with seeded.begin() as db:
        user = db.scalar(select(Account).where(Account.email == "customer1@example.com"))
        user.roles = [r for r in user.roles if r.name == "support_agent"]
    assert client.get("/api/v1/orders", headers=user_token).status_code == 403
    assert client.get("/api/v1/admin/orders", headers=user_token).status_code == 200


def test_seed_preserves_database_edits(seeded):
    from app.scripts.seed import seed

    with seeded.begin() as db:
        account = db.scalar(select(Account).where(Account.email == "customer1@example.com"))
        original_hash = account.password_hash
        assert PasswordHasher().verify(original_hash, "DemoPass123!")
        account.password_hash = PasswordHasher().hash("ChangedPassword!")
        changed_hash = account.password_hash
        account.is_active = False
        account.roles = []
        admin = db.scalar(select(Role).where(Role.name == "admin"))
        admin.permissions = []
    seed()
    with seeded() as db:
        account = db.scalar(select(Account).where(Account.email == "customer1@example.com"))
        assert account.password_hash == changed_hash
        assert not account.is_active
        assert account.roles == []
        assert db.scalar(select(Role).where(Role.name == "admin")).permissions == []
        assert db.scalar(text("SELECT count(*) FROM accounts")) == 12
        assert db.scalar(text("SELECT count(*) FROM orders")) == 25
        assert db.scalar(text("SELECT count(*) FROM customers")) == 10


def test_account_constraints(seeded):
    with seeded() as db:
        original = db.scalar(select(Account).where(Account.email == "customer1@example.com"))
        customer_id = original.customer_id
        account_id = original.id
        role_id = original.roles[0].id
        permission_id = original.roles[0].permissions[0].id
    invalid = [
        Account(email="customer1@example.com", password_hash="hash"),
        Account(email="UPPER@example.com", password_hash="hash"),
        Account(email="new@example.com", password_hash="hash", customer_id=customer_id),
        Account(email="new@example.com", password_hash="hash", customer_id=uuid.uuid4()),
        AccountRole(account_id=account_id, role_id=role_id),
        RolePermission(role_id=role_id, permission_id=permission_id),
    ]
    for row in invalid:
        with seeded() as db, pytest.raises(IntegrityError):
            db.add(row)
            db.commit()


def test_migration_downgrade_preserves_customer_data(seeded):
    with seeded() as db:
        schema = db.scalar(text("SELECT current_schema()"))
    original_url = settings.database_url
    try:
        settings.database_url = original_url + f"?options=-csearch_path={schema}"
        command.downgrade(Config("alembic.ini"), "0001")
        with seeded() as db:
            assert "accounts" not in inspect(db.bind).get_table_names()
            assert db.scalar(text("SELECT count(*) FROM customers")) == 10
            assert db.scalar(text("SELECT count(*) FROM orders")) == 25
        command.upgrade(Config("alembic.ini"), "head")
        with seeded() as db:
            assert db.scalar(text("SELECT count(*) FROM accounts")) == 0
    finally:
        settings.database_url = original_url


def test_login_normalization_and_disabled_accounts(client, seeded):
    assert login(client, email=" CUSTOMER1@EXAMPLE.COM ").status_code == 200
    assert login(client, email="admin1@example.com").status_code == 403
    with seeded.begin() as db:
        db.execute(text("UPDATE accounts SET is_active=false WHERE email='customer1@example.com'"))
    assert login(client).status_code == 401
    assert (
        client.post("/api/v1/auth/login", json={"email": "invalid", "password": "x"}).status_code
        == 422
    )


def test_admin_can_disable_account(client, seeded):
    admin = headers(client, admin=True)
    user = headers(client)
    me = client.get("/api/v1/auth/me", headers=user).json()
    response = client.patch(
        f"/api/v1/admin/accounts/{me['id']}", json={"is_active": False}, headers=admin
    )
    assert response.status_code == 200
    assert "password_hash" not in response.json()
    assert client.get("/api/v1/orders", headers=user).status_code == 401


def test_inactive_products_hidden_from_public_but_visible_to_admin(client, seeded):
    product = ids(seeded)["product"]
    admin = headers(client, admin=True)
    assert client.delete(f"/api/v1/admin/products/{product}", headers=admin).status_code == 204
    assert client.get(f"/api/v1/products/{product}").status_code == 404
    assert client.get(f"/api/v1/admin/products/{product}", headers=admin).status_code == 200


def test_secret_settings_and_compose_contract(monkeypatch):
    monkeypatch.delenv("JWT_SECRET")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="unused")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="unused", jwt_secret="short")
    compose = Path("compose.yaml").read_text()
    # Both migration and API processes import Settings.
    assert compose.count("JWT_SECRET: ${JWT_SECRET:?") == 2
    assert "JWT_SECRET=" in Path(".env.example").read_text()


def test_seeded_permission_values_and_identity_contract(client, seeded):
    """Refactoring identifiers must preserve stored grants and the public response."""
    expected = {
        "user:access",
        "profile:read_own",
        "profile:write_own",
        "orders:create_own",
        "orders:read_own",
        "orders:cancel_own",
        "admin:access",
        "customers:manage",
        "catalog:read_all",
        "catalog:write",
        "inventory:manage",
        "orders:read_all",
        "orders:manage",
        "accounts:manage",
    }
    with seeded() as db:
        assert set(db.scalars(select(Permission.name))) == expected
    identity = client.get("/api/v1/auth/me", headers=headers(client)).json()
    assert identity["roles"] == ["user"]
    assert set(identity["permissions"]) == {
        "user:access",
        "profile:read_own",
        "profile:write_own",
        "orders:create_own",
        "orders:read_own",
        "orders:cancel_own",
    }
