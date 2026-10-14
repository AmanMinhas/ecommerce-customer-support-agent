import json
import uuid

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from sqlalchemy import select, text, update
from test_authorization import headers
from test_chat import BASE, create_conversation

from app.chat import service
from app.models import ChatEvent, ChatMessage, ChatRun


class FakeModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response(messages) if callable(response) else response


@pytest.fixture()
def checkpoints(database):
    from app.scripts.setup_chat import setup_checkpoints

    with database() as db:
        engine = db.bind
    setup_checkpoints(engine)
    setup_checkpoints(engine)  # Deployment initialization is safe to repeat.


def send(client, path, auth, content="Where is my order?", client_id=None):
    return client.post(
        path + "/messages",
        headers=auth,
        json={"client_id": client_id or str(uuid.uuid4()), "content": content},
    )


def test_grounded_tool_answer_and_checkpoint_continuation(client, seeded, checkpoints, monkeypatch):
    tool = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "lookup-1",
                "name": "lookup_order",
                "args": {"order_number": "ORD-100001"},
            }
        ],
    )
    model = FakeModel(
        [
            tool,
            AIMessage(content="Your order is being processed."),
            AIMessage(content="We are discussing ORD-100001."),
        ]
    )
    monkeypatch.setattr(service, "get_model", lambda: model, raising=False)
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    response = send(client, path, auth)
    assert response.status_code == 200
    assert response.json()["run"]["status"] == "completed"
    results = [m for m in model.calls[1] if isinstance(m, ToolMessage)]
    data = json.loads(results[-1].content)
    assert data["order_number"] == "ORD-100001"
    assert "shipping_line1" not in data
    assert send(client, path, auth, "Which order were we discussing?").status_code == 200
    assert any("Your order is being processed" in str(m.content) for m in model.calls[-1])
    transcript = client.get(path + "/messages", headers=auth).json()["items"]
    assert [m["author"] for m in transcript] == ["customer", "assistant", "customer", "assistant"]
    with seeded() as db:
        assert db.scalar(text("SELECT count(*) FROM checkpoints")) > 0


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("private provider error"),
        httpx.ReadTimeout("private provider error"),
        httpx.ConnectError("private provider error"),
    ],
)
def test_failed_request_retry_and_duplicate_submission(
    client, seeded, checkpoints, monkeypatch, failure
):
    model = FakeModel([failure, AIMessage(content="Hello again.")])
    monkeypatch.setattr(service, "get_model", lambda: model, raising=False)
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    client_id = str(uuid.uuid4())
    first = send(client, path, auth, "Hello", client_id).json()
    assert first["run"]["status"] == "failed"
    assert "private provider" not in str(first)
    message_id = first["message"]["id"]
    response = client.post(path + f"/messages/{message_id}/retry", headers=auth)
    assert response.json()["run"]["status"] == "completed"
    assert response.json()["run"]["attempts"] == 2
    assert send(client, path, auth, "Hello", client_id).json()["message"]["id"] == message_id
    assert len(model.calls) == 2
    assert len(client.get(path + "/messages", headers=auth).json()["items"]) == 2


def test_model_handoff_and_human_takeover_during_execution(
    client, seeded, checkpoints, monkeypatch
):
    auth = headers(client)
    admin = headers(client, admin=True)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "handoff-1",
                        "name": "request_support",
                        "args": {"reason": "Needs a refund"},
                    }
                ],
            )
        ]
    )
    monkeypatch.setattr(service, "get_model", lambda: model, raising=False)
    assert send(client, path, auth, "Please refund my order").json()["run"]["status"] == "completed"
    assert client.get(path, headers=auth).json()["status"] == "waiting_for_admin"
    assert send(client, path, auth, "Thank you").json()["run"] is None
    support = path.replace("/api/v1/", "/api/v1/admin/")
    client.post(support + "/claim", headers=admin)
    client.post(support + "/resume", headers=admin)

    def takeover(_messages):
        assert client.post(support + "/claim", headers=admin).status_code == 200
        return AIMessage(content="This outdated AI answer must not appear.")

    monkeypatch.setattr(service, "get_model", lambda: FakeModel([takeover]))
    result = send(client, path, auth, "Are you there?").json()
    assert result["run"]["status"] == "cancelled"
    assert "outdated" not in str(client.get(path + "/messages", headers=auth).json())


def test_tools_cannot_access_other_customers_or_arbitrary_sql(
    client, seeded, checkpoints, monkeypatch
):
    model = FakeModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "1", "name": "lookup_order", "args": {"order_number": "ORD-100002"}},
                    {"id": "2", "name": "run_sql", "args": {"query": "select * from accounts"}},
                    {"id": "3", "name": "list_orders", "args": {"customer_id": str(uuid.uuid4())}},
                ],
            ),
            AIMessage(content="I cannot find that order in your account."),
        ]
    )
    monkeypatch.setattr(service, "get_model", lambda: model, raising=False)
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    response = send(client, path, auth)
    assert response.json()["run"]["status"] == "completed"
    outputs = [json.loads(m.content) for m in model.calls[-1] if isinstance(m, ToolMessage)]
    assert [x["error"] for x in outputs] == ["not_found", "unsupported_tool", "invalid_arguments"]


def test_orphaned_run_retry_and_foreign_message_denial(client, seeded, checkpoints, monkeypatch):
    auth = headers(client)
    other = headers(client, email="customer2@example.com")
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    monkeypatch.setattr(service, "get_model", lambda: FakeModel([TimeoutError()]), raising=False)
    result = send(client, path, auth).json()
    assert result["run"]["status"] == "failed"
    message_id = result["message"]["id"]
    with seeded.begin() as db:
        run = db.scalar(select(ChatRun).where(ChatRun.message_id == uuid.UUID(message_id)))
        run.status = "running"  # Simulate process death; no connection owns the execution lock.
    assert client.post(path + f"/messages/{message_id}/retry", headers=other).status_code == 404
    monkeypatch.setattr(service, "get_model", lambda: FakeModel([AIMessage(content="Recovered.")]))
    assert (
        client.post(path + f"/messages/{message_id}/retry", headers=auth).json()["run"]["status"]
        == "completed"
    )
    with seeded() as db:
        assert (
            len(
                db.scalars(
                    select(ChatMessage).where(ChatMessage.reply_to_id == uuid.UUID(message_id))
                ).all()
            )
            == 1
        )


def test_failed_attempt_does_not_block_after_human_support(
    client, seeded, checkpoints, monkeypatch
):
    auth, admin = headers(client), headers(client, admin=True)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    monkeypatch.setattr(service, "get_model", lambda: FakeModel([TimeoutError()]))
    assert send(client, path, auth).json()["run"]["status"] == "failed"
    client.post(path + "/handoff", headers=auth)
    support = path.replace("/api/v1/", "/api/v1/admin/")
    client.post(support + "/claim", headers=admin)
    client.post(support + "/resume", headers=admin)
    monkeypatch.setattr(
        service, "get_model", lambda: FakeModel([AIMessage(content="Welcome back.")])
    )
    response = send(client, path, auth, "Hello again")
    assert response.status_code == 200
    assert response.json()["run"]["status"] == "completed"


def test_only_one_execution_per_conversation(client, seeded, checkpoints, monkeypatch):
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"

    def concurrent_message(_messages):
        assert send(client, path, auth, "Concurrent").status_code == 409
        return AIMessage(content="First response.")

    monkeypatch.setattr(service, "get_model", lambda: FakeModel([concurrent_message]))
    assert send(client, path, auth, "First question").json()["run"]["status"] == "completed"
    assert len(client.get(path + "/messages", headers=auth).json()["items"]) == 2


def test_checkpoint_recovers_crash_before_reply_publication(
    client, seeded, checkpoints, monkeypatch
):
    model = FakeModel([AIMessage(content="Durable response.")])
    monkeypatch.setattr(service, "get_model", lambda: model)
    original = service.append_message

    def crash(db, conversation, author, *args, **kwargs):
        if author == "assistant":
            raise RuntimeError("simulated crash after checkpoint")
        return original(db, conversation, author, *args, **kwargs)

    monkeypatch.setattr(service, "append_message", crash)
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    with pytest.raises(RuntimeError, match="simulated crash"):
        send(client, path, auth, "Hello")
    message = client.get(path + "/messages", headers=auth).json()["items"][0]
    monkeypatch.setattr(service, "append_message", original)
    assert (
        client.post(path + f"/messages/{message['id']}/retry", headers=auth).json()["run"]["status"]
        == "completed"
    )
    assert len(model.calls) == 1


def test_missing_key_is_a_safe_retryable_failure(client, seeded, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", None)
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    result = send(client, path, auth, "Hello").json()
    assert result["run"]["status"] == "failed"
    assert result["run"]["error_code"] == "assistant_unavailable"
    assert send(client, path, auth, "Another question").status_code == 409
    assert client.post(path + "/handoff", headers=auth).status_code == 200


def test_takeover_then_provider_failure_cannot_reactivate_old_attempt(
    client, seeded, checkpoints, monkeypatch
):
    auth, admin = headers(client), headers(client, admin=True)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    support = path.replace("/api/v1/", "/api/v1/admin/")

    def takeover(_messages):
        client.post(support + "/claim", headers=admin)
        client.post(support + "/resume", headers=admin)
        raise TimeoutError()

    monkeypatch.setattr(service, "get_model", lambda: FakeModel([takeover]))
    assert send(client, path, auth).json()["run"]["status"] == "cancelled"
    monkeypatch.setattr(service, "get_model", lambda: FakeModel([AIMessage(content="New reply.")]))
    assert send(client, path, auth, "New request").status_code == 200


def test_cancelled_attempt_stays_cancelled_when_event_timestamps_are_out_of_order(
    client, seeded, checkpoints, monkeypatch
):
    auth, admin = headers(client), headers(client, admin=True)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    support = path.replace("/api/v1/", "/api/v1/admin/")
    client.post(support + "/claim", headers=admin)
    client.post(support + "/resume", headers=admin)
    with seeded() as db:
        earlier_events = list(db.scalars(select(ChatEvent.id)))

    def takeover(_messages):
        client.post(support + "/claim", headers=admin)
        client.post(support + "/resume", headers=admin)
        with seeded.begin() as db:
            # PostgreSQL now() is transaction-start time, not commit/lock order.
            db.execute(
                update(ChatEvent)
                .where(ChatEvent.id.not_in(earlier_events))
                .values(created_at=text("CURRENT_TIMESTAMP - INTERVAL '1 day'"))
            )
        return AIMessage(content="A cancelled answer must not be published.")

    monkeypatch.setattr(service, "get_model", lambda: FakeModel([takeover]))
    assert send(client, path, auth).json()["run"]["status"] == "cancelled"
    assert "cancelled answer" not in str(client.get(path + "/messages", headers=auth).json())


def test_permission_revocation_suppresses_reply(client, seeded, checkpoints, monkeypatch):
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"

    def revoke(_messages):
        with seeded.begin() as db:
            db.execute(
                text("UPDATE accounts SET is_active=false WHERE email='customer1@example.com'")
            )
        return AIMessage(content="This must not be published.")

    monkeypatch.setattr(service, "get_model", lambda: FakeModel([revoke]))
    assert send(client, path, auth).json()["run"]["status"] == "cancelled"
    with seeded() as db:
        assert db.scalar(select(ChatMessage.id).where(ChatMessage.author == "assistant")) is None


def test_tool_budget_escalates_instead_of_looping_forever(client, seeded, checkpoints, monkeypatch):
    response = AIMessage(
        content="",
        tool_calls=[
            {"id": "lookup", "name": "lookup_order", "args": {"order_number": "ORD-100001"}}
        ],
    )
    model = FakeModel([response] * 4)
    monkeypatch.setattr(service, "get_model", lambda: model)
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    assert send(client, path, auth).json()["run"]["status"] == "completed"
    assert client.get(path, headers=auth).json()["status"] == "waiting_for_admin"
    assert len(model.calls) == 4


def test_message_and_attempt_are_durable_together_before_processing(client, seeded, monkeypatch):
    auth = headers(client)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"

    def crash(*args, **kwargs):
        raise RuntimeError("crash before execution")

    monkeypatch.setattr(service, "process_message", crash)
    with pytest.raises(RuntimeError, match="crash before execution"):
        send(client, path, auth, "Save this question")
    with seeded() as db:
        message = db.scalar(select(ChatMessage).where(ChatMessage.content == "Save this question"))
        assert message is not None
        run = db.scalar(select(ChatRun).where(ChatRun.message_id == message.id))
        assert run is not None
        assert run.status == "running"


def test_retry_cannot_process_a_human_mode_message_after_resume(
    client, seeded, checkpoints, monkeypatch
):
    auth, admin = headers(client), headers(client, admin=True)
    path = f"{BASE}/{create_conversation(client, auth).json()['id']}"
    support = path.replace("/api/v1/", "/api/v1/admin/")
    client.post(path + "/handoff", headers=auth)
    message = send(client, path, auth, "A message for the human").json()["message"]
    client.post(support + "/claim", headers=admin)
    client.post(support + "/resume", headers=admin)
    model = FakeModel([AIMessage(content="This should not be called.")])
    monkeypatch.setattr(service, "get_model", lambda: model)
    assert client.post(path + f"/messages/{message['id']}/retry", headers=auth).status_code == 409
    assert not model.calls
