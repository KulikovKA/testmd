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
from universal_agent_runtime.application.text_stream import (
    TextDeltaCoalescer,
    redact_text,
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
    def test_invalid_final_results_fail_closed(self) -> None:
        native = uuid4()
        for final in (
            None,
            {
                "type": "result",
                "subtype": "error",
                "session_id": str(native),
                "result": "text",
            },
            {
                "type": "result",
                "subtype": "success",
                "session_id": str(uuid4()),
                "result": "text",
            },
            {
                "type": "result",
                "subtype": "success",
                "session_id": str(native),
                "result": None,
            },
            {
                "type": "result",
                "subtype": "success",
                "session_id": str(native),
                "result": {"text": "text"},
            },
        ):
            with self.subTest(final=final):
                parser = QwenJSONLStream(native, None)
                if final is not None:
                    parser.feed(_line(final))
                with self.assertRaises(QwenRunnerFailure) as raised:
                    parser.finish()
                self.assertEqual(
                    raised.exception.code, QwenRunnerErrorCode.PROTOCOL_FAILURE
                )

    def test_mismatch_without_subscriber_is_still_protocol_failure(self) -> None:
        native = uuid4()
        parser = QwenJSONLStream(native, None)
        parser.feed(_start(str(native)) + _partial(str(native), "partial"))
        parser.feed(_result(str(native), "different"))
        with self.assertRaises(QwenRunnerFailure) as raised:
            parser.finish()
        self.assertEqual(raised.exception.code, QwenRunnerErrorCode.PROTOCOL_FAILURE)

    def test_unterminated_line_is_bounded_and_final_line_needs_no_newline(self) -> None:
        native = uuid4()
        parser = QwenJSONLStream(native, None)
        parser.feed(_result(str(native), "answer").rstrip(b"\n"))
        self.assertEqual(parser.finish().response, "answer")
        parser = QwenJSONLStream(native, None)
        with self.assertRaises(QwenRunnerFailure):
            parser.feed(b"x" * 262_145)

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
                def exec_start(self, _exec_id: str, **options: object):
                    self_test.assertEqual(options, {"stream": True, "demux": True})
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

            self_test = self
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


class TextFilterTests(unittest.TestCase):
    def test_redaction_is_independent_of_chunk_boundaries(self) -> None:
        cases = (
            (("ABCDEF",), "hello ABCDEF world", "hello [REDACTED] world"),
            (("ABC", "ABCDEF"), "ABCDEF ABC!", "[REDACTED] [REDACTED]!"),
            (("ABCDEF", "ABC"), "ABCDEF ABC!", "[REDACTED] [REDACTED]!"),
            (
                ("token-one", "token-two"),
                "token-one / token-two",
                "[REDACTED] / [REDACTED]",
            ),
            (("секрет",), "до секрет после", "до [REDACTED] после"),
            (("ABCDEF",), "hello ABC", "hello ABC"),
            (("", "ABC", "ABC"), "ABC", "[REDACTED]"),
            (("RED",), "RED", "[REDACTED]"),
            ((), "ordinary text", "ordinary text"),
        )
        for secrets, raw, expected in cases:
            # Every possible two-chunk boundary, plus one-character arrival.
            for pieces in [(raw[:i], raw[i:]) for i in range(len(raw) + 1)] + [
                tuple(raw)
            ]:
                with self.subTest(secrets=secrets, pieces=pieces):
                    received: list[str] = []
                    redactor = _StreamingRedactor(secrets, received.append)
                    for piece in pieces:
                        redactor.push(piece)
                        self.assertTrue(expected.startswith("".join(received)))
                    redactor.finish()
                    redactor.finish()
                    self.assertEqual("".join(received), expected)
                    self.assertEqual(redact_text(raw, secrets), expected)

    def test_only_possible_secret_suffix_is_delayed(self) -> None:
        received: list[str] = []
        redactor = _StreamingRedactor(("ABCDEF", "ABC"), received.append)
        redactor.push("hello ")
        self.assertEqual(received, ["hello "])
        redactor.push("ABC")
        self.assertEqual(received, ["hello "])
        redactor.push("DEF world")
        self.assertEqual(received, ["hello ", "[REDACTED] world"])
        redactor.finish()
        self.assertEqual(len(received), 2)

    def test_coalescing_by_size_flushes_tail_and_preserves_unicode(self) -> None:
        received: list[str] = []
        coalescer = TextDeltaCoalescer(received.append)
        text = "Для проверки потоковой передачи ответа. " * 3
        for index, character in enumerate(text, start=1):
            coalescer.push(character)
            self.assertEqual(len(received), index // 32)
        coalescer.finish()
        coalescer.finish()
        self.assertEqual("".join(received), text)
        self.assertTrue(all(len(piece) == 32 for piece in received[:-1]))
        self.assertLessEqual(len(received[-1]), 32)

    def test_coalescer_rejects_invalid_size(self) -> None:
        for minimum in (0, -1, True, 1.5):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                TextDeltaCoalescer(lambda _: None, min_characters=minimum)


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

    def __init__(
        self, pieces: tuple[str, ...] = ("hello ", "cookie-", "secret")
    ) -> None:
        self.pieces = pieces
        self.response: str | None = None
        self.before_result = lambda: None

    def run_stream(self, invocation: QwenInvocation, on_delta) -> QwenExecution:
        if self.fail:
            on_delta("partial response before failure")
            raise QwenRunnerFailure(QwenRunnerErrorCode.INFERENCE_UNAVAILABLE)
        for text in self.pieces:
            on_delta(text)
        self.before_result()
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
        return QwenExecution(
            invocation.native_session_id,
            self.response if self.response is not None else "".join(self.pieces),
        )

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
    async def test_callback_failure_during_chunk_or_final_flush_rolls_back(
        self,
    ) -> None:
        for text in ("short tail", "x" * 64):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                adapter = QwenSessionAdapter(
                    QwenSessionConfig(
                        storage_root=root,
                        base_url="http://ollama.example/v1",
                        model="test-model",
                        api_key="cookie-secret",
                    ),
                    runner=_SessionRunner((text,)),
                )
                reference = SessionReference(
                    AgentId("agent-stream"), SessionId("session-stream")
                )
                await adapter.create_session(reference)
                state = (root / "agent-stream" / "session-state.json").read_bytes()
                history = (root / "agent-stream" / "history.jsonl").read_bytes()

                async def reject(delta: AssistantTextDelta) -> None:
                    raise RuntimeError("private callback details")

                with self.assertRaises(InteractionFailure) as raised:
                    await adapter.turn_stream(TurnRequest(reference, "request"), reject)
                self.assertEqual(
                    raised.exception.code, InteractionErrorCode.OPERATION_FAILED
                )
                self.assertNotIn("private callback details", str(raised.exception))
                self.assertEqual(
                    (root / "agent-stream" / "session-state.json").read_bytes(), state
                )
                self.assertEqual(
                    (root / "agent-stream" / "history.jsonl").read_bytes(), history
                )
                self.assertFalse((root / "agent-stream" / ".turn-in-progress").exists())

    async def test_empty_deltas_fall_back_to_authoritative_final_response(self) -> None:
        class FinalOnly(_Interaction):
            async def turn_stream(self, request: TurnRequest, on_delta):
                await on_delta(AssistantTextDelta(""))
                return TurnResult(request.session, 1, "final answer")

        chat, repository, agent_id = _chat(FinalOnly(()))
        turn = chat.begin(agent_id, "request", stream=True)
        events = [
            _event(frame)
            async for frame in ag_ui_events(
                turn, thread_id="thread", run_id="run", heartbeat_seconds=1
            )
            if frame.startswith(b"data: ")
        ]
        self.assertEqual(
            [event["delta"] for event in events if "delta" in event], ["final answer"]
        )
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual(repository.get(agent_id).messages[1].content, "final answer")

    async def test_adapter_redacts_before_coalescing_and_emits_before_result(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = _SessionRunner(tuple("x" * 31 + "ABCDEF" + "y" * 66))
            adapter = QwenSessionAdapter(
                QwenSessionConfig(
                    storage_root=Path(directory),
                    base_url="http://ollama.example/v1",
                    model="test-model",
                    api_key="ABC",
                    sfera_username="ABCDEF",
                    sfera_password="another-secret",
                ),
                runner=runner,
            )
            reference = SessionReference(
                AgentId("agent-stream"), SessionId("session-stream")
            )
            await adapter.create_session(reference)
            received: list[str] = []

            async def collect(delta: AssistantTextDelta) -> None:
                received.append(delta.text)

            runner.before_result = lambda: self.assertGreaterEqual(len(received), 3)
            result = await adapter.turn_stream(
                TurnRequest(reference, "request"), collect
            )
            self.assertEqual(result.response, "x" * 31 + "[REDACTED]" + "y" * 66)
            self.assertEqual("".join(received), result.response)
            self.assertTrue(all(len(piece) >= 32 for piece in received[:-1]))
            self.assertNotIn("ABC", "".join(received))
            self.assertNotIn("DEF", "".join(received))
            history = (Path(directory) / "agent-stream" / "history.jsonl").read_bytes()
            state = (
                Path(directory) / "agent-stream" / "session-state.json"
            ).read_bytes()
            runner.response = "a contradictory final answer"
            with self.assertRaises(InteractionFailure) as raised:
                await adapter.turn_stream(TurnRequest(reference, "next"), collect)
            self.assertEqual(
                raised.exception.code, InteractionErrorCode.PROTOCOL_FAILURE
            )
            self.assertEqual(
                (Path(directory) / "agent-stream" / "history.jsonl").read_bytes(),
                history,
            )
            self.assertEqual(
                (Path(directory) / "agent-stream" / "session-state.json").read_bytes(),
                state,
            )

    async def test_inconsistent_port_deltas_fail_before_history_commit(self) -> None:
        class Inconsistent(_Interaction):
            async def turn_stream(self, request: TurnRequest, on_delta):
                await super().turn_stream(request, on_delta)
                return TurnResult(request.session, 1, "authoritative answer")

        interaction = Inconsistent(("partial", " cumulative partial"))
        interaction.release.set()
        chat, repository, agent_id = _chat(interaction)
        turn = chat.begin(agent_id, "request", stream=True)
        events = [
            _event(frame)
            async for frame in ag_ui_events(
                turn, thread_id="thread", run_id="run", heartbeat_seconds=1
            )
            if frame.startswith(b"data: ")
        ]
        self.assertEqual(events[-1]["type"], "RUN_ERROR")
        self.assertNotIn("TEXT_MESSAGE_END", [event["type"] for event in events])
        record = repository.get(agent_id)
        self.assertEqual(record.messages, ())
        self.assertEqual(
            record.failure.interaction_code, InteractionErrorCode.PROTOCOL_FAILURE
        )

    async def test_provisional_total_is_bounded(self) -> None:
        interaction = _Interaction(("x" * 8192,) * 3)
        interaction.release.set()
        chat, repository, agent_id = _chat(interaction)
        turn = chat.begin(agent_id, "request", stream=True)
        events = [
            _event(frame)
            async for frame in ag_ui_events(
                turn, thread_id="thread", run_id="run", heartbeat_seconds=1
            )
            if frame.startswith(b"data: ")
        ]
        self.assertEqual(events[-1]["type"], "RUN_ERROR")
        self.assertEqual(repository.get(agent_id).messages, ())
        self.assertLessEqual(
            sum(len(event["delta"]) for event in events if "delta" in event), 16_384
        )

    async def test_header_disconnect_and_send_timeout_detach_full_queue(self) -> None:
        for failure in ("header", "timeout", "cancelled"):
            with self.subTest(failure=failure):
                interaction = _Interaction(("x",) * 100)
                interaction.release.set()
                chat, repository, agent_id = _chat(interaction)
                turn = chat.begin(agent_id, "request", stream=True)
                events = ag_ui_events(
                    turn, thread_id="thread", run_id="run", heartbeat_seconds=1
                )
                response = AGUIEventResponse(
                    events, send_timeout_seconds=0.01, turn=turn
                )

                async def send(message, failure=failure) -> None:
                    if failure == "header":
                        raise OSError("header disconnected")
                    if failure == "cancelled":
                        raise asyncio.CancelledError
                    await asyncio.Event().wait()

                try:
                    with self.assertRaises(
                        asyncio.CancelledError if failure == "cancelled" else OSError
                    ):
                        await response.stream_response(send)
                    await asyncio.wait_for(asyncio.shield(turn.task), 2)
                    self.assertEqual(len(repository.get(agent_id).messages), 2)
                finally:
                    turn.deltas.detach()
                    await asyncio.wait_for(turn.task, 2)

    async def test_idle_stream_emits_heartbeat(self) -> None:
        interaction = _Interaction(())
        chat, _, agent_id = _chat(interaction)
        turn = chat.begin(agent_id, "request", stream=True)
        events = ag_ui_events(
            turn, thread_id="thread", run_id="run", heartbeat_seconds=0.01
        )
        self.assertEqual(_event(await anext(events))["type"], "RUN_STARTED")
        try:
            self.assertEqual(
                await asyncio.wait_for(anext(events), 1), b": keep-alive\n\n"
            )
        finally:
            await events.aclose()
            interaction.release.set()
            await asyncio.gather(turn.task, return_exceptions=True)

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
        self.assertEqual(
            [event["type"] for event in observed],
            [
                "RUN_STARTED",
                "TEXT_MESSAGE_START",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_END",
                "RUN_FINISHED",
            ],
        )
        self.assertEqual(
            [event["delta"] for event in observed if "delta" in event],
            [
                "Для",
                " декомпозиции",
                " эпика",
            ],
        )
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
