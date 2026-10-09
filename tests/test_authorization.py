import pytest
from sqlalchemy import text
from test_auth import login


def headers(client, admin=False, email=None):
    email = email or ("admin1@example.com" if admin else "customer1@example.com")
    response = login(client, email=email, admin=admin)
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def ids(database):
    with database() as db:
        return {
            name: str(db.scalar(text(query)))
            for name, query in {
                "customer": "SELECT id FROM customers WHERE email='customer1@example.com'",
                "other_customer": "SELECT id FROM customers WHERE email='customer2@example.com'",
                "other_address": "SELECT a.id FROM addresses a JOIN customers c ON c.id=a.customer_id WHERE c.email='customer2@example.com'",
                "order": "SELECT id FROM orders WHERE order_number='ORD-100001'",
                "other_order": "SELECT id FROM orders WHERE order_number='ORD-100002'",
                "product": "SELECT id FROM products LIMIT 1",
            }.items()
        }


def test_private_routes_require_login(client, seeded):
    data = ids(seeded)
    for path in ["/orders", f"/customers/{data['customer']}", f"/orders/{data['order']}"]:
        assert client.get("/api/v1" + path).status_code in (401, 404)
    assert client.get("/api/v1/orders").status_code == 401
    assert client.get("/api/v1/products").status_code == 200


def test_user_order_list_and_nested_ownership(client, seeded):
    auth = headers(client)
    data = ids(seeded)
    result = client.get("/api/v1/orders", headers=auth)
    assert result.status_code == 200
    assert result.json()["total"] == 3
    assert all(x["customer_id"] == data["customer"] for x in result.json()["items"])
    result = client.get(
        "/api/v1/orders", params={"customer_id": data["other_customer"]}, headers=auth
    )
    assert result.status_code == 200
    assert result.json()["total"] == 3
    for suffix in ["", "/payments", "/shipments", "/status-history"]:
        assert (
            client.get(f"/api/v1/orders/{data['other_order']}{suffix}", headers=auth).status_code
            == 404
        )
        assert (
            client.get(f"/api/v1/orders/{data['order']}{suffix}", headers=auth).status_code == 200
        )
    assert client.get("/api/v1/orders/number/ORD-100002", headers=auth).status_code == 404
    assert (
        client.post(f"/api/v1/orders/{data['other_order']}/cancel", headers=auth).status_code == 404
    )


def test_profile_and_address_ownership(client, seeded):
    auth = headers(client)
    data = ids(seeded)
    assert client.get("/api/v1/customers/me", headers=auth).status_code == 200
    assert (
        client.patch(
            "/api/v1/customers/me", json={"first_name": "Updated"}, headers=auth
        ).status_code
        == 200
    )
    assert client.get("/api/v1/customers/me/addresses", headers=auth).status_code == 200
    path = f"/api/v1/addresses/{data['other_address']}"
    assert client.patch(path, json={"city": "Changed"}, headers=auth).status_code == 404
    assert client.delete(path, headers=auth).status_code == 404


def test_admin_routes_and_removed_privileged_aliases(client, seeded):
    data = ids(seeded)
    user = headers(client)
    admin = headers(client, admin=True)
    assert client.get("/api/v1/admin/orders", headers=user).status_code == 403
    assert client.get("/api/v1/admin/orders", headers=admin).json()["total"] == 25
    payload = {"name": "New", "slug": "new"}
    assert client.post("/api/v1/categories", json=payload).status_code == 405
    assert client.post("/api/v1/admin/categories", json=payload, headers=user).status_code == 403
    assert client.post("/api/v1/admin/categories", json=payload, headers=admin).status_code == 201
    assert client.patch(
        f"/api/v1/orders/{data['order']}/status", json={"status": "confirmed"}, headers=user
    ).status_code in (404, 405)
    assert (
        client.post(f"/api/v1/orders/{data['order']}/payments", json={}, headers=user).status_code
        == 405
    )
    assert client.get("/api/v1/admin/accounts", headers=admin).status_code == 200
    assert client.get("/api/v1/admin/customers", headers=admin).status_code == 200


def test_order_creation_derives_identity(client, seeded):
    auth = headers(client)
    data = ids(seeded)
    address = client.get("/api/v1/customers/me/addresses", headers=auth)
    assert address.status_code == 200
    payload = {
        "shipping_address_id": address.json()[0]["id"],
        "items": [{"product_id": data["product"], "quantity": 1}],
    }
    created = client.post("/api/v1/orders", json=payload, headers=auth)
    assert created.status_code == 201
    assert created.json()["customer_id"] == data["customer"]
    assert (
        client.post(
            "/api/v1/orders", json={**payload, "customer_id": data["other_customer"]}, headers=auth
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/orders",
            json={**payload, "shipping_address_id": data["other_address"]},
            headers=auth,
        ).status_code
        == 400
    )


def test_database_permission_changes_affect_existing_token(client, seeded):
    admin = headers(client, admin=True)
    assert client.get("/api/v1/admin/orders", headers=admin).status_code == 200
    with seeded.begin() as db:
        db.execute(
            text(
                "DELETE FROM role_permissions WHERE permission_id=(SELECT id FROM permissions WHERE name='orders:read_all')"
            )
        )
    assert client.get("/api/v1/admin/orders", headers=admin).status_code == 403
    with seeded.begin() as db:
        db.execute(text("UPDATE accounts SET is_active=false WHERE email='admin1@example.com'"))
    assert client.get("/api/v1/auth/me", headers=admin).status_code == 401


@pytest.mark.parametrize(
    "path", ["/api/v1/admin/products", "/api/v1/admin/orders", "/api/v1/admin/accounts"]
)
def test_admin_access_alone_does_not_grant_capabilities(client, seeded, path):
    auth = headers(client, admin=True)
    with seeded.begin() as db:
        db.execute(
            text(
                "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name <> 'admin:access')"
            )
        )
    assert client.get(path, headers=auth).status_code == 403


def test_own_address_lifecycle_and_order_cancellation(client, seeded):
    auth = headers(client)
    payload = {
        "line1": "100 Test St",
        "city": "Austin",
        "state": "TX",
        "postal_code": "78701",
        "is_default": True,
    }
    response = client.post("/api/v1/customers/me/addresses", headers=auth, json=payload)
    assert response.status_code == 201
    address_id = response.json()["id"]
    result = client.get("/api/v1/customers/me/addresses", headers=auth).json()
    assert sum(address["is_default"] for address in result) == 1
    assert (
        client.patch(
            f"/api/v1/addresses/{address_id}", headers=auth, json={"city": "Dallas"}
        ).status_code
        == 200
    )
    order = ids(seeded)["order"]
    response = client.post(f"/api/v1/orders/{order}/cancel", headers=auth)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert client.post(f"/api/v1/orders/{order}/cancel", headers=auth).status_code == 409
    assert client.delete(f"/api/v1/addresses/{address_id}", headers=auth).status_code == 204


def test_missing_customer_link_cannot_expose_all_orders(client, seeded):
    auth = headers(client)
    with seeded.begin() as db:
        db.execute(text("UPDATE accounts SET customer_id=NULL WHERE email='customer1@example.com'"))
    assert client.get("/api/v1/orders", headers=auth).status_code == 403
    assert client.get("/api/v1/customers/me", headers=auth).status_code == 403


def test_schema_exposes_bearer_security_and_hides_legacy_mutations(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/v1/orders"]["get"]["security"] == [{"HTTPBearer": []}]
    assert schema["paths"]["/api/v1/admin/orders"]["get"]["security"] == [{"HTTPBearer": []}]
    assert "post" not in schema["paths"]["/api/v1/products"]
    assert "/api/v1/orders/{order_id}/status" not in schema["paths"]
