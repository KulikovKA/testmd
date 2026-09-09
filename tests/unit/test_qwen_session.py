import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar

import pytest

from universal_agent_runtime.adapters.qwen_session import (
    QWEN_IMAGE,
    QwenCommandRunner,
    QwenExecution,
    QwenInvocation,
    QwenRunnerErrorCode,
    QwenRunnerFailure,
    QwenSessionAdapter,
    QwenSessionConfig,
)
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
    InteractionFailure,
)
from universal_agent_runtime.application.ports.interaction_values import (
    SessionReference,
    TurnRequest,
)
from universal_agent_runtime.domain.identifiers import AgentId, SessionId

T = TypeVar("T")


def run(awaitable: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(awaitable)


class FakeQwenRunner(QwenCommandRunner):
    def __init__(
        self,
        responses: list[str] | None = None,
        failure: QwenRunnerFailure | None = None,
    ) -> None:
        self.responses = responses or ["ok"]
        self.failure = failure
        self.invocations: list[QwenInvocation] = []
        self.closed = False

    def run(self, invocation: QwenInvocation) -> QwenExecution:
        self.invocations.append(invocation)
        if self.failure is not None:
            raise self.failure
        transcript = (
            invocation.qwen_home
            / "projects"
            / "workspace"
            / "chats"
            / f"{invocation.native_session_id}.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        with transcript.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(
                    {
                        "session_id": str(invocation.native_session_id),
                        "resume": invocation.resume,
                    }
                )
                + "\n"
            )
        response = (
            self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        )
        return QwenExecution(invocation.native_session_id, response)

    def close(self) -> None:
        self.closed = True


def session(agent: str = "agent-a", value: str = "session-a") -> SessionReference:
    return SessionReference(AgentId(agent), SessionId(value))


def config(root: Path, **changes: object) -> QwenSessionConfig:
    values: dict[str, object] = {
        "storage_root": root,
        "base_url": "http://host.docker.internal:11434/v1",
        "model": "qwen3:0.6b",
        "api_key": "ollama",
    }
    values.update(changes)
    return QwenSessionConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "localhost:11434/v1"},
        {"base_url": "http://user:password@host/v1"},
        {"model": ""},
        {"api_key": ""},
        {"wall_time_seconds": 0},
        {"max_tokens": 63},
        {"max_session_turns": 1},
        {"max_history_characters": 0},
        {"max_transcript_bytes": 0},
        {"image": ""},
    ],
)
def test_invalid_adapter_configuration_is_rejected(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    with pytest.raises((TypeError, ValueError)):
        config(tmp_path / "sessions", **changes)


def test_filesystem_root_cannot_be_session_storage(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        config(Path(tmp_path.anchor))


def test_multiple_turns_and_reopened_adapter_use_one_logical_session(
    tmp_path: Path,
) -> None:
    reference = session()
    first_runner = FakeQwenRunner(["ACK", "CODEWORD_BLUE"])
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=first_runner)

    created = run(adapter.create_session(reference))
    first = run(
        adapter.turn(TurnRequest(reference, "Remember CODEWORD_BLUE and reply ACK."))
    )
    second = run(
        adapter.turn(TurnRequest(reference, "What codeword did I ask you to remember?"))
    )

    assert created.completed_turns == 0
    assert first.completed_turns == 1
    assert second.completed_turns == 2
    assert second.response == "CODEWORD_BLUE"
    assert first_runner.invocations[0].resume is False
    assert first_runner.invocations[1].resume is True
    native_ids = {
        invocation.native_session_id for invocation in first_runner.invocations
    }
    assert len(native_ids) == 1
    assert "CODEWORD_BLUE" in first_runner.invocations[1].prompt
    run(adapter.close())

    reopened_runner = FakeQwenRunner(["CODEWORD_BLUE"])
    reopened = QwenSessionAdapter(config(tmp_path / "sessions"), runner=reopened_runner)
    observation = run(reopened.create_session(reference))
    third = run(
        reopened.turn(
            TurnRequest(reference, "Repeat the remembered codeword once more.")
        )
    )

    assert observation.completed_turns == 2
    assert third.completed_turns == 3
    assert reopened_runner.invocations[0].resume is True
    assert reopened_runner.invocations[0].native_session_id in native_ids
    assert "What codeword did I ask you to remember?" in (
        reopened_runner.invocations[0].prompt
    )
    run(reopened.close())
    assert first_runner.closed is True
    assert reopened_runner.closed is True


def test_agent_session_and_workspace_are_isolated(tmp_path: Path) -> None:
    runner = FakeQwenRunner(["ok-a", "ok-b"])
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    first = session("agent-a", "session-a")
    second = session("agent-b", "session-b")
    run(adapter.create_session(first))
    run(adapter.create_session(second))
    first_workspace = tmp_path / "sessions" / "agent-a" / "workspace"
    (first_workspace / "private.txt").write_text("AGENT_A_ONLY", encoding="utf-8")

    run(adapter.turn(TurnRequest(first, "first")))
    run(adapter.turn(TurnRequest(second, "second")))

    first_invocation, second_invocation = runner.invocations
    assert first_invocation.workspace != second_invocation.workspace
    assert first_invocation.qwen_home != second_invocation.qwen_home
    assert (second_invocation.workspace / "private.txt").exists() is False
    assert first_invocation.native_session_id != second_invocation.native_session_id
    wrong = session("agent-a", "session-other")
    with pytest.raises(InteractionFailure) as conflict:
        run(adapter.create_session(wrong))
    assert conflict.value.code is InteractionErrorCode.CONFLICT
    with pytest.raises(InteractionFailure) as missing:
        run(adapter.turn(TurnRequest(wrong, "must not cross session boundary")))
    assert missing.value.code is InteractionErrorCode.NOT_FOUND
    run(adapter.close())


def test_delete_removes_all_session_artifacts_and_is_idempotent(
    tmp_path: Path,
) -> None:
    reference = session()
    runner = FakeQwenRunner()
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    run(adapter.create_session(reference))
    run(adapter.turn(TurnRequest(reference, "hello")))
    agent_directory = tmp_path / "sessions" / reference.agent_id.value
    assert agent_directory.exists()

    assert run(adapter.delete_session(reference)).session == reference
    assert not agent_directory.exists()
    assert run(adapter.delete_session(reference)).session == reference
    with pytest.raises(InteractionFailure) as missing:
        run(adapter.turn(TurnRequest(reference, "must not recreate")))
    assert missing.value.code is InteractionErrorCode.NOT_FOUND
    run(adapter.close())


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_missing_or_corrupt_native_transcript_is_diagnostic(
    tmp_path: Path, damage: str
) -> None:
    reference = session()
    runner = FakeQwenRunner()
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    run(adapter.create_session(reference))
    run(adapter.turn(TurnRequest(reference, "hello")))
    transcript = next(
        (tmp_path / "sessions" / "agent-a" / "qwen-home").rglob("*.jsonl")
    )
    if damage == "missing":
        transcript.unlink()
        expected = InteractionErrorCode.NOT_FOUND
    else:
        transcript.write_text("not-json\n", encoding="utf-8")
        expected = InteractionErrorCode.CORRUPT_STATE

    with pytest.raises(InteractionFailure) as captured:
        run(adapter.turn(TurnRequest(reference, "must fail")))
    assert captured.value.code is expected
    assert len(runner.invocations) == 1
    run(adapter.close())


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda value: "{not-json", InteractionErrorCode.CORRUPT_STATE),
        (
            lambda value: json.dumps({**json.loads(value), "schema_version": 999}),
            InteractionErrorCode.INCOMPATIBLE_STATE,
        ),
        (
            lambda value: json.dumps({**json.loads(value), "model": "other"}),
            InteractionErrorCode.INCOMPATIBLE_STATE,
        ),
    ],
)
def test_corrupt_or_incompatible_manifest_does_not_start_a_new_session(
    tmp_path: Path,
    mutate: Callable[[str], str],
    expected: InteractionErrorCode,
) -> None:
    reference = session()
    runner = FakeQwenRunner()
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    run(adapter.create_session(reference))
    manifest = tmp_path / "sessions" / "agent-a" / "session-state.json"
    manifest.write_text(mutate(manifest.read_text(encoding="utf-8")), encoding="utf-8")

    with pytest.raises(InteractionFailure) as captured:
        run(adapter.turn(TurnRequest(reference, "must fail")))
    assert captured.value.code is expected
    assert runner.invocations == []
    run(adapter.close())


def test_corrupt_history_is_rejected_before_qwen_runs(tmp_path: Path) -> None:
    reference = session()
    runner = FakeQwenRunner()
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    run(adapter.create_session(reference))
    history = tmp_path / "sessions" / "agent-a" / "history.jsonl"
    history.write_text("{not-json\n", encoding="utf-8")

    with pytest.raises(InteractionFailure) as captured:
        run(adapter.turn(TurnRequest(reference, "must fail")))
    assert captured.value.code is InteractionErrorCode.CORRUPT_STATE
    assert runner.invocations == []
    run(adapter.close())


def test_history_limit_is_rejected_without_silent_truncation(tmp_path: Path) -> None:
    reference = session()
    runner = FakeQwenRunner()
    adapter = QwenSessionAdapter(
        config(tmp_path / "sessions", max_history_characters=5),
        runner=runner,
    )
    run(adapter.create_session(reference))

    with pytest.raises(InteractionFailure) as captured:
        run(adapter.turn(TurnRequest(reference, "hello")))
    assert captured.value.code is InteractionErrorCode.INCOMPATIBLE_STATE
    assert runner.invocations == []
    run(adapter.close())


def test_credential_value_is_never_persisted(tmp_path: Path) -> None:
    reference = session()
    secret = "TASK006_TEST_CREDENTIAL_MUST_NOT_PERSIST"
    runner = FakeQwenRunner()
    adapter = QwenSessionAdapter(
        config(tmp_path / "sessions", api_key=secret), runner=runner
    )
    run(adapter.create_session(reference))
    run(adapter.turn(TurnRequest(reference, "synthetic message")))
    agent_directory = tmp_path / "sessions" / "agent-a"

    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in agent_directory.rglob("*")
        if path.is_file()
    )
    assert secret not in persisted
    run(adapter.close())


@pytest.mark.parametrize(
    "runner_code,expected",
    [
        (
            QwenRunnerErrorCode.INFERENCE_UNAVAILABLE,
            InteractionErrorCode.INFERENCE_UNAVAILABLE,
        ),
        (QwenRunnerErrorCode.TIMEOUT, InteractionErrorCode.TIMEOUT),
        (
            QwenRunnerErrorCode.PROTOCOL_FAILURE,
            InteractionErrorCode.PROTOCOL_FAILURE,
        ),
        (
            QwenRunnerErrorCode.OPERATION_FAILED,
            InteractionErrorCode.OPERATION_FAILED,
        ),
    ],
)
def test_qwen_failures_are_translated_without_backend_details(
    tmp_path: Path,
    runner_code: QwenRunnerErrorCode,
    expected: InteractionErrorCode,
) -> None:
    reference = session()
    runner = FakeQwenRunner(failure=QwenRunnerFailure(runner_code))
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    run(adapter.create_session(reference))

    with pytest.raises(InteractionFailure) as captured:
        run(adapter.turn(TurnRequest(reference, "hello")))
    assert captured.value.code is expected
    assert (
        str(captured.value)
        == f"turn: {expected.value} (agent=agent-a, session=session-a)"
    )
    run(adapter.close())


def test_session_values_are_validated() -> None:
    with pytest.raises(ValueError):
        SessionId("../other")
    with pytest.raises(ValueError):
        TurnRequest(session(), "")
    with pytest.raises(ValueError):
        TurnRequest(session(), "x" * 16_385)


def test_pinned_qwen_image_is_used_by_default(tmp_path: Path) -> None:
    assert config(tmp_path / "sessions").image == QWEN_IMAGE
