"""Streaming deltas remain provisional until the owned turn commits."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests.test_skill_packages import _SkillClient, _SkillContainer
from universal_agent_runtime.adapters.docker_agent_qwen import DockerAgentQwenRunner
from universal_agent_runtime.adapters.in_memory_agent_repository import (
    InMemoryAgentRepository,
)
from universal_agent_runtime.adapters.qwen_session import (
    QwenExecution,
    QwenInvocation,
    QwenJSONLStream,
    QwenRunnerErrorCode,
    QwenRunnerFailure,
    QwenSessionAdapter,
    QwenSessionConfig,
    _StreamingRedactor,
)
from universal_agent_runtime.ag_ui import AGUIEventResponse, ag_ui_events
from universal_agent_runtime.application.agent_chat import (
    AgentChatService,
    ChatConfiguration,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentConfigurationSnapshot,
    AgentRecord,
)
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
    InteractionFailure,
    InteractionOperation,
)
from universal_agent_runtime.application.ports.interaction_values import (
    AssistantTextDelta,
    SessionReference,
    TurnRequest,
    TurnResult,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    ResourceLimits,
)
from universal_agent_runtime.domain.agent import AgentLifecycleState
from universal_agent_runtime.domain.identifiers import AgentId, SessionId, WorkspaceId


def _line(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")


def _partial(session_id: str, text: str) -> bytes:
    return _line(
        {
            "type": "stream_event",
            "session_id": session_id,
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        }
    )


def _start(session_id: str) -> bytes:
    return _line(
        {
            "type": "stream_event",
            "session_id": session_id,
            "event": {"type": "message_start", "message": {"role": "assistant"}},
        }
    ) + _line(
        {
            "type": "stream_event",
            "session_id": session_id,
            "event": {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text"},
            },
        }
    )


def _result(session_id: str, text: str) -> bytes:
    return _line(
        {
            "type": "result",
            "subtype": "success",
            "session_id": session_id,
            "result": text,
        }
    )


class QwenStreamTests(unittest.TestCase):
    def test_split_lines_multibyte_and_ignored_events(self) -> None:
        native = uuid4()
        received: list[str] = []
        parser = QwenJSONLStream(native, received.append)
        data = (
            _line({"type": "system", "message": "system-secret"})
            + b"malformed line\n"
            + b'UAR_METRIC {"kind":"mcp_tool"}\n'
            + _start(str(native))
            + _line(
                {
                    "type": "stream_event",
                    "session_id": str(native),
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "thinking_delta",
                            "thinking": "reasoning-secret",
                        },
                    },
                }
            )
            + _partial(str(native), "Для")
            + _partial(str(native), " декомпозиции")
            + _partial(str(native), " эпика")
            + _line({"type": "tool_call", "arguments": "tool-secret"})
            + _result(str(native), "Для декомпозиции эпика")
        )
        for byte in data:
            parser.feed(bytes((byte,)))
        self.assertEqual(parser.finish().response, "Для декомпозиции эпика")
        self.assertEqual(received, ["Для", " декомпозиции", " эпика"])
        self.assertEqual(parser.event_types, {"tool_call": 1, "result": 1})
        self.assertEqual(parser.mcp_metrics, [{"kind": "mcp_tool"}])

    def test_mismatch_is_protocol_failure_and_secrets_across_deltas_are_redacted(
        self,
    ) -> None:
        native = uuid4()
        parser = QwenJSONLStream(native, lambda _: None)
        parser.feed(_start(str(native)) + _partial(str(native), "first"))
        parser.feed(_result(str(native), "different"))
        with self.assertRaises(QwenRunnerFailure):
            parser.finish()
        received: list[str] = []
        redactor = _StreamingRedactor(("cookie-secret",), received.append)
        redactor.push("hello cookie-")
        redactor.push("secret world")
        redactor.finish()
        self.assertEqual("".join(received), "hello [REDACTED] world")

    def test_docker_exec_delivers_delta_before_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "agent-one" / "workspace"
            qwen_home = root / "agent-one" / "qwen-home"
            workspace.mkdir(parents=True)
            qwen_home.mkdir(parents=True)
            native = uuid4()
            container = _SkillContainer(native, [])
            timeline: list[str] = []

            class Client(_SkillClient):
                def exec_start(self, _exec_id: str, **_: object):
                    yield (
                        _start(str(native)) + _partial(str(native), "one"),
                        b'UAR_METRIC {"kind":"mcp_',
                    )
                    timeline.append("after_first_chunk")
                    yield (
                        _partial(str(native), " two") + _result(str(native), "one two"),
                        b'tool","tool_name":"get_task","duration_ms":5,"success":true}\n',
                    )

                def exec_inspect(self, _exec_id: str) -> dict[str, int]:
                    timeline.append("inspect")
                    return {"ExitCode": 0}

            runner = DockerAgentQwenRunner(
                QwenSessionConfig(
                    storage_root=root / "sessions",
                    base_url="http://ollama.example/v1",
                    model="test-model",
                    api_key="test-placeholder",
                ),
                workspace="/workspace",
                user="10001:10001",
                client=Client(container),
            )
            with patch(
                "universal_agent_runtime.adapters.docker_agent_qwen._emit_mcp_metrics"
            ) as metrics:
                execution = runner.run_stream(
                    QwenInvocation(qwen_home, workspace, native, "prompt", False),
                    lambda text: timeline.append(text),
                )
            self.assertEqual(execution.response, "one two")
            self.assertEqual(timeline, ["one", "after_first_chunk", " two", "inspect"])
            self.assertEqual(metrics.call_args.args[1][0]["tool_name"], "get_task")
            self.assertIn("--include-partial-messages", container.qwen_command)
            self.assertIn("stream-json", container.qwen_command)


class _Interaction:
    def __init__(self, pieces: tuple[str, ...], *, fail: bool = False) -> None:
        self.pieces = pieces
        self.fail = fail
        self.release = asyncio.Event()
        self.emitted = asyncio.Event()
        self.emitted_count = 0

    async def turn_stream(self, request: TurnRequest, on_delta):
        for piece in self.pieces:
            await on_delta(AssistantTextDelta(piece))
            self.emitted_count += 1
            self.emitted.set()
        await self.release.wait()
        if self.fail:
            raise InteractionFailure(
                InteractionOperation.TURN,
                request.session,
                InteractionErrorCode.INFERENCE_UNAVAILABLE,
            )
        return TurnResult(request.session, 1, "".join(self.pieces))


class _SessionRunner:
    fail = False

    def run_stream(self, invocation: QwenInvocation, on_delta) -> QwenExecution:
        if self.fail:
            on_delta("partial response before failure")
            raise QwenRunnerFailure(QwenRunnerErrorCode.INFERENCE_UNAVAILABLE)
        for text in ("hello ", "cookie-", "secret"):
            on_delta(text)
        transcript = (
            invocation.qwen_home
            / "projects"
            / "workspace"
            / "chats"
            / f"{invocation.native_session_id}.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            '{"type":"user"}\n{"type":"assistant"}\n', encoding="utf-8"
        )
        return QwenExecution(invocation.native_session_id, "hello cookie-secret")

    def run(self, invocation: QwenInvocation) -> QwenExecution:
        return self.run_stream(invocation, lambda _: None)

    def close(self) -> None:
        pass


def _chat(
    interaction: _Interaction,
) -> tuple[AgentChatService, InMemoryAgentRepository, AgentId]:
    agent_id = AgentId("agent-stream")
    workspace_id = WorkspaceId("workspace-stream")
    resources = ResourceLimits(1, 1024)
    repository = InMemoryAgentRepository()
    repository.add(
        AgentRecord(
            agent_id,
            workspace_id,
            SessionReference(agent_id, SessionId("session-stream")),
            "create-stream",
            AgentConfigurationSnapshot("qwen-agent-image", resources),
            CreateRuntimeRequest(agent_id, workspace_id, "qwen-agent-image", resources),
            AgentLifecycleState.READY,
        )
    )
    return (
        AgentChatService(interaction, repository, ChatConfiguration()),
        repository,
        agent_id,
    )


def _event(frame: bytes) -> dict[str, object]:
    return json.loads(frame.removeprefix(b"data: "))


class AGUIIncrementalTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_stream_redacts_and_rolls_back_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _SessionRunner()
            adapter = QwenSessionAdapter(
                QwenSessionConfig(
                    storage_root=root,
                    base_url="http://ollama.example/v1",
                    model="test-model",
                    api_key="cookie-secret",
                ),
                runner=runner,
            )
            reference = SessionReference(
                AgentId("agent-stream"), SessionId("session-stream")
            )
            await adapter.create_session(reference)
            deltas: list[str] = []

            async def collect(delta: AssistantTextDelta) -> None:
                deltas.append(delta.text)

            result = await adapter.turn_stream(
                TurnRequest(reference, "request"), collect
            )
            self.assertEqual(result.response, "hello [REDACTED]")
            self.assertEqual("".join(deltas), result.response)
            history = (root / "agent-stream" / "history.jsonl").read_bytes()
            state = (root / "agent-stream" / "session-state.json").read_bytes()
            runner.fail = True
            with self.assertRaises(InteractionFailure):
                await adapter.turn_stream(TurnRequest(reference, "next"), collect)
            self.assertEqual(
                (root / "agent-stream" / "history.jsonl").read_bytes(), history
            )
            self.assertEqual(
                (root / "agent-stream" / "session-state.json").read_bytes(), state
            )
            self.assertFalse((root / "agent-stream" / ".turn-in-progress").exists())

    async def test_deltas_precede_commit_and_message_id_is_stable(self) -> None:
        interaction = _Interaction(("Для", " декомпозиции", " эпика"))
        chat, repository, agent_id = _chat(interaction)
        turn = chat.begin(agent_id, "decompose", stream=True)
        events = ag_ui_events(
            turn, thread_id="thread", run_id="run", heartbeat_seconds=1
        )
        first = _event(await events.__anext__())
        self.assertEqual(first["type"], "RUN_STARTED")
        self.assertFalse(turn.task.done())
        observed = [first]
        while (
            len(
                [event for event in observed if event["type"] == "TEXT_MESSAGE_CONTENT"]
            )
            < 3
        ):
            frame = await asyncio.wait_for(events.__anext__(), 2)
            if frame.startswith(b"data: "):
                observed.append(_event(frame))
        self.assertFalse(turn.task.done())
        self.assertEqual(repository.get(agent_id).state, AgentLifecycleState.BUSY)
        self.assertEqual(repository.get(agent_id).messages, ())
        interaction.release.set()
        observed.extend(
            [_event(frame) async for frame in events if frame.startswith(b"data: ")]
        )
        self.assertEqual(
            [event["type"] for event in observed].count("TEXT_MESSAGE_START"), 1
        )
        self.assertEqual(
            [event["type"] for event in observed].count("TEXT_MESSAGE_END"), 1
        )
        self.assertEqual(observed[-1]["type"], "RUN_FINISHED")
        assistant = repository.get(agent_id).messages[1]
        self.assertEqual(assistant.content, "Для декомпозиции эпика")
        self.assertEqual(
            "".join(
                str(event["delta"])
                for event in observed
                if event["type"] == "TEXT_MESSAGE_CONTENT"
            ),
            assistant.content,
        )
        self.assertEqual(
            {event["messageId"] for event in observed if "messageId" in event},
            {assistant.message_id},
        )

    async def test_failure_after_partial_has_no_history(self) -> None:
        interaction = _Interaction(("partial",), fail=True)
        chat, repository, agent_id = _chat(interaction)
        turn = chat.begin(agent_id, "request", stream=True)
        events = ag_ui_events(
            turn, thread_id="thread", run_id="run", heartbeat_seconds=1
        )
        observed = [_event(await events.__anext__())]
        while not any(event["type"] == "TEXT_MESSAGE_CONTENT" for event in observed):
            observed.append(_event(await asyncio.wait_for(events.__anext__(), 2)))
        interaction.release.set()
        observed.extend(
            [_event(frame) async for frame in events if frame.startswith(b"data: ")]
        )
        self.assertEqual(observed[-1]["type"], "RUN_ERROR")
        self.assertEqual(repository.get(agent_id).messages, ())

    async def test_disconnect_detaches_without_cancelling_turn(self) -> None:
        interaction = _Interaction(("one", " two"))
        chat, repository, agent_id = _chat(interaction)
        turn = chat.begin(agent_id, "request", stream=True)
        events = ag_ui_events(
            turn, thread_id="thread", run_id="run", heartbeat_seconds=1
        )
        response = AGUIEventResponse(events, send_timeout_seconds=1)

        async def disconnected_send(message) -> None:
            if message["type"] == "http.response.body":
                raise OSError("client disconnected")

        with self.assertRaises(OSError):
            await response.stream_response(disconnected_send)
        interaction.release.set()
        await asyncio.wait_for(turn.task, 2)
        self.assertEqual(repository.get(agent_id).state, AgentLifecycleState.READY)
        self.assertEqual(len(repository.get(agent_id).messages), 2)

    async def test_slow_client_queue_is_bounded_and_detach_unblocks_producer(
        self,
    ) -> None:
        interaction = _Interaction(tuple("x" for _ in range(30)))
        chat, repository, agent_id = _chat(interaction)
        turn = chat.begin(agent_id, "request", stream=True)
        events = ag_ui_events(
            turn, thread_id="thread", run_id="run", heartbeat_seconds=1
        )
        await events.__anext__()
        for _ in range(100):
            if turn.deltas.queue.full():
                break
            await asyncio.sleep(0)
        self.assertEqual(turn.deltas.queue.qsize(), 16)
        self.assertFalse(turn.task.done())
        await events.aclose()
        interaction.release.set()
        await asyncio.wait_for(turn.task, 2)
        self.assertEqual(len(repository.get(agent_id).messages), 2)
