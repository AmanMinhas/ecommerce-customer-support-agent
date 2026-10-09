from sqlalchemy import text
from test_authorization import headers, ids


def test_catalog_inventory_and_customer_management(client, seeded):
    auth = headers(client, admin=True)
    category = client.post(
        "/api/v1/admin/categories", headers=auth, json={"name": "Testing", "slug": "testing"}
    ).json()
    payload = {
        "category_id": category["id"],
        "sku": "TEST-SKU",
        "name": "Test product",
        "price": "12.50",
        "quantity_available": 5,
    }
    response = client.post("/api/v1/admin/products", headers=auth, json=payload)
    assert response.status_code == 201
    product = response.json()["id"]
    assert client.post("/api/v1/admin/products", headers=auth, json=payload).status_code == 409
    assert (
        client.patch(
            f"/api/v1/admin/products/{product}", headers=auth, json={"price": "15.00"}
        ).status_code
        == 200
    )
    response = client.patch(
        f"/api/v1/admin/products/{product}/inventory", headers=auth, json={"quantity_available": 8}
    )
    assert response.status_code == 200
    assert response.json()["quantity_available"] == 8
    assert (
        client.patch(
            f"/api/v1/admin/products/{product}/inventory",
            headers=auth,
            json={"quantity_available": -1},
        ).status_code
        == 422
    )
    profile = {"email": "new@example.com", "first_name": "New", "last_name": "Customer"}
    response = client.post("/api/v1/admin/customers", headers=auth, json=profile)
    assert response.status_code == 201
    customer = response.json()["id"]
    assert client.get(f"/api/v1/admin/customers/{customer}", headers=auth).status_code == 200
    assert (
        client.patch(
            f"/api/v1/admin/customers/{customer}", headers=auth, json={"phone": "+1-202-555-1001"}
        ).status_code
        == 200
    )


def test_order_payment_and_shipment_admin_operations(client, seeded):
    auth = headers(client, admin=True)
    order = ids(seeded)["order"]
    base = "/api/v1/admin/orders/" + order
    response = client.patch(base + "/status", headers=auth, json={"status": "confirmed"})
    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert (
        client.patch(base + "/status", headers=auth, json={"status": "delivered"}).status_code
        == 409
    )
    assert (
        client.post(
            base + "/payments", headers=auth, json={"transaction_reference": "test-payment"}
        ).status_code
        == 201
    )
    response = client.post(
        base + "/shipments",
        headers=auth,
        json={"carrier": "Test carrier", "tracking_number": "TEST-TRACK"},
    )
    assert response.status_code == 201
    assert (
        client.patch(
            "/api/v1/admin/shipments/" + response.json()["id"],
            headers=auth,
            json={"status": "delivered"},
        ).status_code
        == 200
    )


def test_admin_cancellation_transition_restores_inventory_once(client, seeded):
    user = headers(client)
    admin = headers(client, admin=True)
    data = ids(seeded)
    address = client.get("/api/v1/customers/me/addresses", headers=user).json()[0]["id"]
    inventory_path = f"/api/v1/admin/products/{data['product']}/inventory"
    before = client.get(inventory_path, headers=admin).json()
    response = client.post(
        "/api/v1/orders",
        headers=user,
        json={
            "shipping_address_id": address,
            "items": [{"product_id": data["product"], "quantity": 2}],
        },
    )
    assert response.status_code == 201
    order_id = response.json()["id"]
    status_path = f"/api/v1/admin/orders/{order_id}/status"
    response = client.patch(
        status_path, headers=admin, json={"status": "cancelled", "note": "Requested"}
    )
    assert response.status_code == 200
    after = client.get(inventory_path, headers=admin).json()
    assert after["quantity_available"] == before["quantity_available"]
    assert after["quantity_reserved"] == before["quantity_reserved"]
    assert client.patch(status_path, headers=admin, json={"status": "cancelled"}).status_code == 409
    history = client.get(f"/api/v1/admin/orders/{order_id}/status-history", headers=admin).json()
    assert history[-1]["note"] == "Requested"


def test_grants_are_checked_for_every_admin_operation(client, seeded):
    auth = headers(client, admin=True)
    data = ids(seeded)
    with seeded.begin() as db:
        db.execute(
            text(
                "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name <> 'admin:access')"
            )
        )
    requests = [
        ("post", "/categories", {"name": "Forbidden", "slug": "forbidden"}),
        ("post", "/products", {"sku": "FORBIDDEN", "name": "Forbidden", "price": "10.00"}),
        ("patch", f"/products/{data['product']}", {"price": "11.00"}),
        ("delete", f"/products/{data['product']}", None),
        ("get", f"/products/{data['product']}/inventory", None),
        ("patch", f"/products/{data['product']}/inventory", {"quantity_available": 3}),
        ("get", "/customers", None),
        ("get", f"/customers/{data['customer']}", None),
        ("get", f"/orders/{data['order']}/payments", None),
        ("get", f"/orders/{data['order']}/shipments", None),
        ("post", f"/orders/{data['order']}/payments", {}),
        ("post", f"/orders/{data['order']}/shipments", {"carrier": "X", "tracking_number": "X"}),
        ("patch", f"/orders/{data['order']}/status", {"status": "confirmed"}),
        ("post", f"/orders/{data['order']}/cancel", None),
    ]
    for method, path, payload in requests:
        assert (
            client.request(method, "/api/v1/admin" + path, headers=auth, json=payload).status_code
            == 403
        )
