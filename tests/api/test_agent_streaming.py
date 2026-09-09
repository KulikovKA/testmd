"""SSE order, ownership, disconnect and backpressure without a real model."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message as ASGIMessage

from tests.api.test_agent_lifecycle_api import _app, _environment
from tests.unit.test_agent_chat import GatedInteraction
from tests.unit.test_agent_lifecycle import _ReadyRuntime, _service
from universal_agent_runtime.application.agent_chat import (
    AgentChatService,
    ChatConfiguration,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleFailure,
    CreateAgentCommand,
)
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
)
from universal_agent_runtime.configuration import (
    ApplicationSettings,
    ConfigurationError,
)
from universal_agent_runtime.domain.agent import AgentLifecycleState
from universal_agent_runtime.http_streaming import (
    StreamEvent,
    TurnEventResponse,
    turn_events,
)


def _events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")
    ]


def test_stream_commit_matches_json_history_and_never_attaches_to_foreign_turn(
    tmp_path: Path,
) -> None:
    app, _, interaction = _app(tmp_path)
    with TestClient(app) as client:  # type: ignore[arg-type]
        agent = client.post("/agents", json={"request_id": "one"}).json()["agent_id"]
        other = client.post("/agents", json={"request_id": "two"}).json()["agent_id"]
        path = f"/agents/{agent}"
        assert (
            client.post(
                path + "/messages/stream", json={"content": "stopped"}
            ).status_code
            == 409
        )
        client.post(path + "/start")
        response = client.post(
            path + "/messages/stream",
            json={"content": "do-not-expose-this-placeholder\nevent: forged"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        events = _events(response.text)
        assert [e["data"]["type"] for e in events] == [
            "started",
            "content",
            "completed",
        ]
        assert [e["sequence"] for e in events] == [1, 2, 3]
        assert all(e["agent_id"] == agent for e in events)
        assert all(e["turn_id"] == response.headers["x-turn-id"] for e in events)
        assert all(StreamEvent.model_validate(e) for e in events)
        assert "do-not-expose-this-placeholder" not in response.text
        assert "\nevent: forged\n" not in response.text
        history = client.get(path + "/messages").json()["messages"]
        assert events[-1]["data"]["message_ids"] == [m["message_id"] for m in history]
        assert events[1]["data"]["content"] == history[1]["content"]
        assert history[0]["turn_id"] == events[0]["turn_id"]
        assert client.get(f"/agents/{other}/messages").json()["messages"] == []
        # There is no subscribe-by-turn or global broadcast API to cross-wire.
        assert (
            client.get(
                f"/agents/{other}/events?turn_id={events[0]['turn_id']}"
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/agents/{other}/messages/stream",
                json={"content": "hi", "turn_id": events[0]["turn_id"]},
            ).status_code
            == 422
        )
        replay = client.post(
            path + "/messages/stream",
            headers={"Last-Event-ID": events[0]["turn_id"] + ":1"},
            json={"content": "retry"},
        )
        assert (
            replay.status_code == 409
            and replay.json()["error"]["code"] == "replay_not_supported"
        )
        assert len(client.get(path + "/messages").json()["messages"]) == 2
        normal = client.post(path + "/messages", json={"content": "next"})
        assert normal.status_code == 201
        assert normal.json()["messages"][1]["sequence"] == 4
        assert interaction.create_calls == 2
        schema = client.get("/openapi.json").json()["paths"][
            "/agents/{agent_id}/messages/stream"
        ]["post"]
        assert "text/event-stream" in schema["responses"]["200"]["content"]
        assert "x-event-schema" in schema


@pytest.mark.parametrize(
    "code",
    [InteractionErrorCode.INFERENCE_UNAVAILABLE, InteractionErrorCode.CORRUPT_STATE],
)
def test_stream_failure_is_terminal_redacted_and_not_committed(
    tmp_path: Path, code: InteractionErrorCode
) -> None:
    app, _, interaction = _app(tmp_path)
    with TestClient(app) as client:  # type: ignore[arg-type]
        agent = client.post("/agents", json={"request_id": "one"}).json()["agent_id"]
        path = f"/agents/{agent}"
        client.post(path + "/start")
        interaction.turn_failure = code
        response = client.post(path + "/messages/stream", json={"content": "hi"})
        assert response.status_code == 200  # Headers preceded the adapter failure.
        events = _events(response.text)
        assert [e["data"]["type"] for e in events] == ["started", "error"]
        assert events[-1]["sequence"] == 2
        assert events[-1]["data"]["interaction_code"] == code.value
        assert client.get(path + "/messages").json()["messages"] == []
        assert events[-1]["data"]["state"] == client.get(path).json()["state"]


def test_keepalive_disconnect_and_json_overlap_share_one_owned_task() -> None:
    async def scenario() -> None:
        interaction = GatedInteraction()
        lifecycle, repository = _service(_ReadyRuntime(), interaction, "one", "two")
        chat = AgentChatService(interaction, repository, ChatConfiguration())
        record = (await lifecycle.create(CreateAgentCommand("one"))).agent
        await lifecycle.start(record.agent_id)
        turn = chat.begin(record.agent_id, "one")
        events = turn_events(turn, 0.001)
        assert _events((await anext(events)).decode())[0]["sequence"] == 1
        await interaction.entered.wait()
        assert await anext(events) == b": keep-alive\n\n"
        for operation in (
            chat.send(record.agent_id, "duplicate"),
            lifecycle.stop(record.agent_id),
        ):
            with pytest.raises(AgentLifecycleFailure):
                await operation
        await events.aclose()
        assert not turn.task.cancelled()
        assert lifecycle.inspect(record.agent_id).state is AgentLifecycleState.BUSY
        other = (await lifecycle.create(CreateAgentCommand("two"))).agent
        await lifecycle.start(other.agent_id)
        assert (await chat.send(other.agent_id, "other"))[-1].content == "other"
        interaction.release.set()
        await chat.close()
        assert len(chat.history(record.agent_id).messages) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("disconnect", [False, True])
def test_blocked_or_disconnected_network_never_cancels_inference(
    disconnect: bool,
) -> None:
    async def scenario() -> None:
        interaction = GatedInteraction()
        lifecycle, repository = _service(_ReadyRuntime(), interaction, "one")
        chat = AgentChatService(interaction, repository, ChatConfiguration())
        record = (await lifecycle.create(CreateAgentCommand("one"))).agent
        await lifecycle.start(record.agent_id)
        turn = chat.begin(record.agent_id, "one")
        response = TurnEventResponse(
            turn, heartbeat_seconds=0.001, send_timeout_seconds=0.01
        )
        writes = 0

        async def send(message: ASGIMessage) -> None:
            nonlocal writes
            writes += 1
            if message["type"] == "http.response.body":
                if disconnect:
                    raise OSError("connection closed")
                await asyncio.Event().wait()

        with pytest.raises(OSError):
            await response.stream_response(send)
        assert writes == 2
        assert not turn.task.done()
        assert lifecycle.inspect(record.agent_id).state is AgentLifecycleState.BUSY
        interaction.release.set()
        await chat.close()
        assert lifecycle.inspect(record.agent_id).state is AgentLifecycleState.READY

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_stream_timing_configuration_is_validated(tmp_path: Path, value: str) -> None:
    for name in ("UAR_STREAM_HEARTBEAT_SECONDS", "UAR_STREAM_SEND_TIMEOUT_SECONDS"):
        with pytest.raises(ConfigurationError):
            ApplicationSettings.from_environment(
                {**_environment(tmp_path), name: value}
            )


def test_asgi_disconnect_stops_delivery_but_retains_busy_until_commit() -> None:
    async def scenario() -> None:
        interaction = GatedInteraction()
        lifecycle, repository = _service(_ReadyRuntime(), interaction, "one")
        chat = AgentChatService(interaction, repository, ChatConfiguration())
        record = (await lifecycle.create(CreateAgentCommand("one"))).agent
        await lifecycle.start(record.agent_id)
        turn = chat.begin(record.agent_id, "one")
        response = TurnEventResponse(
            turn, heartbeat_seconds=0.01, send_timeout_seconds=1
        )
        started = asyncio.Event()
        bodies = []

        async def send(message: ASGIMessage) -> None:
            if message["type"] == "http.response.body":
                bodies.append(message["body"])
                started.set()

        async def receive() -> ASGIMessage:
            await started.wait()
            return {"type": "http.disconnect"}

        await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
        assert len(bodies) == 1
        assert not turn.task.cancelled()
        assert lifecycle.inspect(record.agent_id).state is AgentLifecycleState.BUSY
        interaction.release.set()
        await chat.close()
        assert len(chat.history(record.agent_id).messages) == 2

    asyncio.run(scenario())
