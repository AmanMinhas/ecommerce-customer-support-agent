from sqlalchemy import inspect, text


def login(client, email="customer1@example.com", admin=False, password="DemoPass123!"):
    prefix = "/api/v1/admin" if admin else "/api/v1"
    return client.post(prefix + "/auth/login", json={"email": email, "password": password})


def test_auth_schema(database):
    with database() as db:
        names = inspect(db.bind).get_table_names()
    assert {"accounts", "roles", "permissions", "account_roles", "role_permissions"} <= set(names)


def test_seeded_user_login_and_identity(client, seeded):
    response = login(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 1800
    me = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + body["access_token"]})
    assert me.status_code == 200
    assert me.json()["email"] == "customer1@example.com"
    assert "password_hash" not in me.json()


def test_wrong_password_and_admin_permission(client, seeded):
    assert login(client, password="wrong").status_code == 401
    assert login(client, email="missing@example.com").status_code == 401
    assert login(client, admin=True).status_code == 403
    assert login(client, email="admin1@example.com", admin=True).status_code == 200


def test_seed_accounts_count(seeded):
    with seeded() as db:
        assert db.scalar(text("SELECT count(*) FROM accounts")) == 12
