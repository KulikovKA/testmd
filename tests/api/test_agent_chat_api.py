"""Public transport contract using deterministic interaction/runtime doubles."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.api.test_agent_lifecycle_api import _app, _environment
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
)
from universal_agent_runtime.configuration import (
    ApplicationSettings,
    ConfigurationError,
)


def test_chat_contract_context_redaction_history_and_delete(tmp_path: Path) -> None:
    app, _, interaction = _app(tmp_path)
    with TestClient(app) as client:  # type: ignore[arg-type]
        first = client.post("/agents", json={"request_id": "one"}).json()
        other = client.post("/agents", json={"request_id": "two"}).json()
        path = f"/agents/{first['agent_id']}"
        assert (
            client.post(path + "/messages", json={"content": "hello"}).status_code
            == 409
        )
        assert client.post(path + "/start").status_code == 200
        sent = client.post(
            path + "/messages", json={"content": "do-not-expose-this-placeholder"}
        )
        assert sent.status_code == 201, sent.text
        messages = sent.json()["messages"]
        assert messages[0]["content"] == "[REDACTED]"
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert [m["sequence"] for m in messages] == [1, 2]
        assert messages[0]["turn_id"] == messages[1]["turn_id"]
        assert "do-not-expose-this-placeholder" not in sent.text
        assert client.post(path + "/stop").status_code == 200
        assert client.post(path + "/start").status_code == 200
        second = client.post(path + "/messages", json={"content": "remember"})
        assert second.status_code == 201
        assert "[REDACTED]" in second.json()["messages"][-1]["content"]
        history = client.get(path + "/messages?limit=2").json()
        assert history["messages"] == messages
        assert history["next_after"] == 2
        assert (
            client.get(path + "/messages?after=2").json()["messages"]
            == second.json()["messages"]
        )
        assert (
            client.get(f"/agents/{other['agent_id']}/messages").json()["messages"] == []
        )
        assert client.get(path).json()["session_id"] == first["session_id"]
        assert interaction.create_calls == 2
        assert client.post(path + "/stop").status_code == 200
        assert client.delete(path).status_code == 204
        assert client.get(path + "/messages").status_code == 404
        assert (
            client.post(path + "/messages", json={"content": "deleted"}).status_code
            == 404
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"content": " "},
        {"content": "\x00"},
        {"content": 12},
        {"content": "x" * 16385},
        {"content": "ok", "session_id": "foreign"},
    ],
)
def test_message_validation_is_redacted(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    app, _, _ = _app(tmp_path)
    with TestClient(app) as client:  # type: ignore[arg-type]
        response = client.post("/agents/valid/messages", json=payload)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "request_invalid"


def test_error_codes_and_openapi(tmp_path: Path) -> None:
    app, _, interaction = _app(tmp_path)
    with TestClient(app) as client:  # type: ignore[arg-type]
        agent = client.post("/agents", json={"request_id": "one"}).json()["agent_id"]
        path = f"/agents/{agent}"
        client.post(path + "/start")
        for code, status in (
            (InteractionErrorCode.INFERENCE_UNAVAILABLE, 503),
            (InteractionErrorCode.TIMEOUT, 504),
            (InteractionErrorCode.TOOL_FAILED, 502),
        ):
            interaction.turn_failure = code
            response = client.post(path + "/messages", json={"content": "test"})
            assert response.status_code == status
            assert response.json()["error"]["state"] == "READY"
            assert response.json()["error"]["operation"] == "message"
            assert client.get(path + "/messages").json()["messages"] == []
        for query in ("limit=51", "limit=0", "after=-1", "after=invalid"):
            assert client.get(path + "/messages?" + query).status_code == 422
        assert client.get("/agents/bad%20id/messages").status_code == 422
        schema = client.get("/openapi.json").json()
        operations = schema["paths"]["/agents/{agent_id}/messages"]
        assert set(operations) == {"get", "post"}
        assert "201" in operations["post"]["responses"]
        assert "504" in operations["post"]["responses"]
        assert "MessageResponse" in schema["components"]["schemas"]


@pytest.mark.parametrize(
    "name,value",
    [
        ("UAR_CHAT_MAX_MESSAGE_CHARACTERS", "16385"),
        ("UAR_CHAT_MAX_RESPONSE_CHARACTERS", "0"),
        ("UAR_CHAT_MAX_HISTORY_MESSAGES", "1"),
        ("UAR_CHAT_MAX_HISTORY_PAGE_SIZE", "invalid"),
    ],
)
def test_chat_configuration_rejects_invalid_limits_before_composition(
    tmp_path: Path, name: str, value: str
) -> None:
    environment = _environment(tmp_path)
    environment[name] = value
    with pytest.raises(ConfigurationError):
        ApplicationSettings.from_environment(environment)
