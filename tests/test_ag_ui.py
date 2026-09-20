from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_runtime_driver import _DockerClient, _environment
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
    InteractionFailure,
    InteractionOperation,
)
from universal_agent_runtime.application.ports.interaction_values import (
    AssistantTextDelta,
    DeleteSessionResult,
    SessionObservation,
    SessionReference,
    TurnRequest,
    TurnResult,
)
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application


class _Interaction:
    def __init__(self) -> None:
        self._sessions: set[SessionReference] = set()
        self._turns: dict[SessionReference, list[str]] = {}
        self.turn_failure: InteractionErrorCode | None = None

    async def create_session(self, reference: SessionReference) -> SessionObservation:
        self._sessions.add(reference)
        return SessionObservation(reference, 0)

    async def turn(self, request: TurnRequest) -> TurnResult:
        if self.turn_failure is not None:
            raise InteractionFailure(
                InteractionOperation.TURN, request.session, self.turn_failure
            )
        turns = self._turns.setdefault(request.session, [])
        turns.append(request.message)
        return TurnResult(request.session, len(turns), "answer: " + request.message)

    async def delete_session(self, reference: SessionReference) -> DeleteSessionResult:
        self._sessions.discard(reference)
        return DeleteSessionResult(reference)

    async def close(self) -> None:
        self._sessions.clear()


class _StreamingInteraction(_Interaction):
    async def turn_stream(self, request: TurnRequest, on_delta) -> TurnResult:
        pieces = ("answer:", " ", request.message)
        for piece in pieces:
            await on_delta(AssistantTextDelta(piece))
        turns = self._turns.setdefault(request.session, [])
        turns.append(request.message)
        return TurnResult(request.session, len(turns), "".join(pieces))


def _events(response_text: str) -> list[dict[str, object]]:
    return [
        json.loads(line[6:])
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


class AGUIHttpTests(unittest.TestCase):
    def _app(self, root: Path, interaction: _Interaction | None = None):
        settings = ApplicationSettings.from_environment(
            {
                **_environment("docker"),
                "UAR_DOCKER_NETWORK_MODE": "none",
                "UAR_QWEN_SESSION_STORAGE_ROOT": str(root),
                "UAR_SKILL_REGISTRY_ROOT": str(root / "skills"),
            }
        )
        runtime = DockerRuntime(
            {
                "qwen-agent-image": DockerWorkload(
                    "uar-agent:0.1.0", ("serve",), "10001:10001"
                )
            },
            secret_resolver=lambda _secret_id: "test-secret",
            client=_DockerClient(),
        )
        self.interaction = interaction or _Interaction()
        return create_application(
            compose_application(settings, runtime=runtime, interaction=self.interaction)
        )

    @staticmethod
    def _input(content: str = "decompose this") -> dict[str, object]:
        return {
            "threadId": "thread-1",
            "runId": "run-1",
            "state": {},
            "messages": [{"id": "message-1", "role": "user", "content": content}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }

    def test_valid_run_uses_official_lifecycle_and_committed_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self._app(Path(directory))) as client:
                created = client.post("/agents", json={"request_id": "ag-ui"})
                agent_id = created.json()["agent_id"]
                self.assertEqual(
                    client.post(f"/agents/{agent_id}/start").status_code, 200
                )
                response = client.post(
                    f"/ag-ui/agents/{agent_id}/run", json=self._input()
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(
                    response.headers["content-type"].startswith("text/event-stream")
                )
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["x-accel-buffering"], "no")
                events = _events(response.text)
        self.assertEqual(
            [event["type"] for event in events],
            [
                "RUN_STARTED",
                "TEXT_MESSAGE_START",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_END",
                "RUN_FINISHED",
            ],
        )
        self.assertEqual(
            events[0], {"type": "RUN_STARTED", "threadId": "thread-1", "runId": "run-1"}
        )
        self.assertEqual(events[1]["role"], "assistant")
        self.assertEqual(events[1]["messageId"], events[2]["messageId"])
        self.assertEqual(events[2]["messageId"], events[3]["messageId"])
        self.assertEqual(events[2]["delta"], "answer: decompose this")

    def test_http_run_forwards_three_live_deltas_with_one_public_message_id(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            TestClient(self._app(Path(directory), _StreamingInteraction())) as client,
        ):
            created = client.post("/agents", json={"request_id": "ag-ui-stream"})
            agent_id = created.json()["agent_id"]
            self.assertEqual(client.post(f"/agents/{agent_id}/start").status_code, 200)
            response = client.post(f"/ag-ui/agents/{agent_id}/run", json=self._input())
            history = client.get(f"/agents/{agent_id}/messages")
        events = _events(response.text)
        content = [event for event in events if event["type"] == "TEXT_MESSAGE_CONTENT"]
        self.assertEqual(
            [event["delta"] for event in content], ["answer:", " ", "decompose this"]
        )
        self.assertEqual(
            [event["type"] for event in events].count("TEXT_MESSAGE_START"), 1
        )
        self.assertEqual(
            [event["type"] for event in events].count("TEXT_MESSAGE_END"), 1
        )
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual(
            {event["messageId"] for event in events if "messageId" in event},
            {content[0]["messageId"]},
        )
        self.assertEqual(
            history.json()["messages"][1]["content"],
            "".join(event["delta"] for event in content),
        )

    def test_unknown_or_unready_agent_ends_with_ag_ui_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self._app(Path(directory))) as client:
                unknown = client.post("/ag-ui/agents/missing/run", json=self._input())
                created = client.post("/agents", json={"request_id": "unready"})
                unready = client.post(
                    f"/ag-ui/agents/{created.json()['agent_id']}/run",
                    json=self._input(),
                )
        for response, code in ((unknown, "not_found"), (unready, "invalid_state")):
            events = _events(response.text)
            self.assertEqual(
                [event["type"] for event in events], ["RUN_STARTED", "RUN_ERROR"]
            )
            self.assertEqual(events[-1]["code"], code)

    def test_legacy_stream_remains_a_single_committed_response(self) -> None:
        class LegacyInteraction(_StreamingInteraction):
            async def turn_stream(self, request: TurnRequest, on_delta) -> TurnResult:
                raise AssertionError("legacy endpoint must use the committed turn port")

        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self._app(Path(directory), LegacyInteraction())) as client:
                agent_id = client.post("/agents", json={"request_id": "legacy"}).json()[
                    "agent_id"
                ]
                self.assertEqual(
                    client.post(f"/agents/{agent_id}/start").status_code, 200
                )
                response = client.post(
                    f"/agents/{agent_id}/messages/stream", json={"content": "hello"}
                )
                history = client.get(f"/agents/{agent_id}/messages").json()["messages"]
                replay = client.post(
                    f"/agents/{agent_id}/messages/stream",
                    json={"content": "again"},
                    headers={"Last-Event-ID": "old-turn:1"},
                )
                self.assertEqual(replay.status_code, 409)
                self.assertEqual(
                    len(client.get(f"/agents/{agent_id}/messages").json()["messages"]),
                    2,
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        events = _events(response.text)
        self.assertEqual(
            [event["data"]["type"] for event in events],
            ["started", "content", "completed"],
        )
        self.assertEqual(events[0]["data"]["content_mode"], "committed_response")
        self.assertEqual(events[1]["data"]["content"], history[1]["content"])
        self.assertEqual(events[1]["data"]["message_id"], history[1]["message_id"])

    def test_malformed_run_input_is_an_ag_ui_error_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self._app(Path(directory))) as client:
                response = client.post(
                    "/ag-ui/agents/missing/run",
                    json={"threadId": "thread-1", "runId": "run-1", "messages": []},
                )
        events = _events(response.text)
        self.assertEqual(
            [event["type"] for event in events], ["RUN_STARTED", "RUN_ERROR"]
        )
        self.assertEqual(events[-1]["code"], "request_invalid")

    def test_failed_agent_execution_ends_with_ag_ui_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self._app(Path(directory))) as client:
                created = client.post("/agents", json={"request_id": "failed-turn"})
                agent_id = created.json()["agent_id"]
                self.assertEqual(
                    client.post(f"/agents/{agent_id}/start").status_code, 200
                )
                self.interaction.turn_failure = (
                    InteractionErrorCode.INFERENCE_UNAVAILABLE
                )
                response = client.post(
                    f"/ag-ui/agents/{agent_id}/run", json=self._input()
                )
        events = _events(response.text)
        self.assertEqual(
            [event["type"] for event in events], ["RUN_STARTED", "RUN_ERROR"]
        )
        self.assertEqual(events[-1]["code"], "inference_unavailable")


if __name__ == "__main__":
    unittest.main()
