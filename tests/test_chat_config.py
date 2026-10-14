from pathlib import Path

from pydantic import SecretStr

from app.chat import runtime
from app.config import Settings, settings
from app.main import app


def test_gemini_key_is_explicit_and_never_uses_openai(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    config = Settings(_env_file=None)
    assert config.gemini_api_key.get_secret_value() == "synthetic-test-key"
    assert "synthetic-test-key" not in repr(config)
    captured = {}

    def model(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(runtime, "ChatGoogleGenerativeAI", model)
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("synthetic-test-key"))
    runtime.get_model()
    assert captured["google_api_key"].get_secret_value() == "synthetic-test-key"
    assert captured["vertexai"] is False
    assert captured["timeout"] <= 30


def test_deployment_config_passes_gemini_and_initializes_checkpoints():
    assert "GEMINI_API_KEY:" in Path("compose.yaml").read_text()
    assert "app.scripts.setup_chat" in Path("compose.yaml").read_text()
    assert "GEMINI_API_KEY=" in Path(".env.example").read_text()


def test_chat_response_contracts_are_documented():
    document = app.openapi()
    for prefix in ["/api/v1/chat", "/api/v1/admin/chat"]:
        path = document["paths"][prefix + "/conversations/{conversation_id}/messages"]
        for method, schema in [("get", "MessagePage"), ("post", "SendResult")]:
            assert path[method]["responses"]["200"]["content"]["application/json"]["schema"] == {
                "$ref": f"#/components/schemas/{schema}"
            }
