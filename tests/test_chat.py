import uuid

from sqlalchemy import inspect
from test_authorization import headers

BASE = "/api/v1/chat/conversations"


def create_conversation(client, auth, **values):
    return client.post(BASE, headers=auth, json={"id": str(uuid.uuid4()), **values})


def test_chat_schema(database):
    with database() as db:
        assert {"chat_conversations", "chat_messages", "chat_runs", "chat_events"} <= set(
            inspect(db.bind).get_table_names()
        )


def test_customer_conversations_are_private_and_creation_is_idempotent(client, seeded):
    auth = headers(client)
    other = headers(client, email="customer2@example.com")
    assert client.get(BASE).status_code == 401
    payload = {"id": str(uuid.uuid4()), "title": "My delivery"}
    response = client.post(BASE, json=payload, headers=auth)
    assert response.status_code == 201
    conversation = response.json()
    assert conversation["status"] == "ai_active"
    assert client.post(BASE, json=payload, headers=auth).json()["id"] == conversation["id"]
    assert client.post(BASE, json=payload, headers=other).status_code == 409
    assert client.get(BASE, headers=auth).json()["total"] == 1
    assert client.get(BASE, headers=other).json()["total"] == 0
    assert client.get(f"{BASE}/{conversation['id']}", headers=other).status_code == 404
    assert client.get(BASE, params={"page_size": 101}, headers=auth).status_code == 422


def test_chat_capabilities_are_seeded(client, seeded):
    user = client.get("/api/v1/auth/me", headers=headers(client)).json()
    admin = client.get("/api/v1/auth/me", headers=headers(client, admin=True)).json()
    assert "chat:own" in user["permissions"]
    assert "chat:manage" in admin["permissions"]


def test_handoff_messages_claim_and_resolution(client, seeded):
    auth = headers(client)
    admin = headers(client, admin=True)
    admin2 = headers(client, admin=True, email="admin2@example.com")
    conversation = create_conversation(client, auth).json()
    path = f"{BASE}/{conversation['id']}"
    support = path.replace("/api/v1/", "/api/v1/admin/")
    assert client.post(path + "/handoff", headers=auth).status_code == 200
    assert client.get(path, headers=auth).json()["status"] == "waiting_for_admin"
    payload = {"client_id": str(uuid.uuid4()), "content": "Please help with delivery"}
    first = client.post(path + "/messages", headers=auth, json=payload)
    assert first.status_code == 200
    assert first.json()["run"] is None
    assert client.post(path + "/messages", headers=auth, json=payload).json() == first.json()
    assert (
        client.post(
            path + "/messages", headers=auth, json={**payload, "content": "Changed"}
        ).status_code
        == 409
    )
    assert client.get(support, headers=auth).status_code == 403
    inbox = client.get("/api/v1/admin/chat/conversations", headers=admin).json()
    assert inbox["total"] == 1
    assert client.post(support + "/claim", headers=admin).status_code == 200
    assert client.get(support, headers=admin).json()["can_reply"] is True
    assert client.get(support, headers=admin2).json()["can_reply"] is False
    assert client.post(support + "/claim", headers=admin2).status_code == 409
    reply = {"client_id": str(uuid.uuid4()), "content": "I am checking your delivery."}
    assert client.post(support + "/messages", headers=admin2, json=reply).status_code == 409
    assert client.post(support + "/messages", headers=admin, json=reply).status_code == 200
    assert client.post(support + "/messages", headers=admin, json=reply).status_code == 200
    messages = client.get(path + "/messages", headers=auth).json()
    assert [m["author"] for m in messages["items"]] == ["system", "customer", "admin"]
    assert [m["sequence"] for m in messages["items"]] == [1, 2, 3]
    assert (
        client.get(path + "/messages?after=2&limit=1", headers=auth).json()["items"][0]["content"]
        == reply["content"]
    )
    latest = client.get(path + "/messages?latest=true&limit=2", headers=auth).json()
    assert [m["sequence"] for m in latest["items"]] == [2, 3]
    assert latest["has_more"] is True
    older = client.get(path + "/messages?before=2&limit=2", headers=auth).json()
    assert [m["sequence"] for m in older["items"]] == [1]
    assert older["has_more"] is False
    assert client.post(support + "/resolve", headers=admin).status_code == 200
    assert (
        client.post(
            path + "/messages",
            headers=auth,
            json={"client_id": str(uuid.uuid4()), "content": "Hello"},
        ).status_code
        == 409
    )
    assert client.post(support + "/resume", headers=admin).json()["status"] == "ai_active"


def test_message_validation_ownership_and_admin_takeover(client, seeded):
    auth = headers(client)
    other = headers(client, email="customer2@example.com")
    admin = headers(client, admin=True)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    payload = {"client_id": str(uuid.uuid4()), "content": "Hello"}
    for suffix in ("/messages", "/handoff"):
        assert client.post(path + suffix, headers=other, json=payload).status_code == 404
    assert client.get(path + "/messages", headers=other).status_code == 404
    assert (
        client.post(
            path + "/messages", headers=auth, json={**payload, "content": "   "}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/messages", headers=auth, json={**payload, "content": "x" * 4001}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/messages", headers=auth, json={**payload, "customer_id": str(uuid.uuid4())}
        ).status_code
        == 422
    )
    support = path.replace("/api/v1/", "/api/v1/admin/")
    assert client.post(support + "/claim", headers=admin).json()["status"] == "human_active"
    assert client.post(path + "/messages", headers=auth, json=payload).json()["run"] is None


def test_chat_revocation_applies_to_existing_conversations(client, seeded):
    from sqlalchemy import text

    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    with seeded.begin() as db:
        db.execute(
            text(
                "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE name='chat:own')"
            )
        )
    assert client.get(path, headers=auth).status_code == 403
