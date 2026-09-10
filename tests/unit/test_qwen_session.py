import asyncio
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import pytest

from universal_agent_runtime.adapters.qwen_session import (
    QWEN_IMAGE,
    DockerQwenCommandRunner,
    QwenCommandRunner,
    QwenExecution,
    QwenInvocation,
    QwenRunnerErrorCode,
    QwenRunnerFailure,
    QwenSessionAdapter,
    QwenSessionConfig,
    _append_task_results,
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


def test_verified_task_results_are_appended_without_model_paraphrase() -> None:
    task: dict[str, object] = {
        "id": "task-0001",
        "title": "Release Alpha",
        "description": "",
        "status": "open",
        "parent_id": None,
        "subtask_ids": [],
        "version": 1,
    }
    execution = QwenExecution(uuid4(), "Model answer with [parent_id]")
    result = _append_task_results(
        execution,
        (json.dumps({"operation": "create_task", "task": task}) + "\n").encode(),
        ("create_task",),
        65_536,
    )
    assert result.response.startswith(execution.response)
    assert json.dumps(task, separators=(",", ":")) in result.response


@pytest.mark.parametrize(
    "raw,allowed",
    [
        (b"not-json\n", ("create_task",)),
        (
            b'{"operation":"create_task","task":{"id":"invented"}}\n',
            ("create_task",),
        ),
        (
            b'{"operation":"create_task","task":{}}\n',
            ("get_task",),
        ),
        (
            b"".join(
                (
                    b'{"operation":"create_task","task":{"id":"task-0001",',
                    b'"title":"x","description":"","status":[],',
                    b'"parent_id":null,"subtask_ids":[],"version":1}}\n',
                )
            ),
            ("create_task",),
        ),
    ],
)
def test_untrusted_task_result_journal_is_rejected(
    raw: bytes, allowed: tuple[str, ...]
) -> None:
    with pytest.raises(QwenRunnerFailure) as failure:
        _append_task_results(QwenExecution(uuid4(), "answer"), raw, allowed, 65_536)
    assert failure.value.code is QwenRunnerErrorCode.PROTOCOL_FAILURE


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
        {"reasoning_directive": "fast"},
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


@pytest.mark.parametrize("successful_first", [False, True])
def test_failed_native_turn_rolls_back_before_retry(
    tmp_path: Path, successful_first: bool
) -> None:
    class MutatingFailure(FakeQwenRunner):
        fail = False

        def run(self, invocation: QwenInvocation) -> QwenExecution:
            result = super().run(invocation)
            if self.fail:
                raise QwenRunnerFailure(QwenRunnerErrorCode.INFERENCE_UNAVAILABLE)
            return result

    runner = MutatingFailure()
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    reference = session()
    run(adapter.create_session(reference))
    if successful_first:
        run(adapter.turn(TurnRequest(reference, "committed")))
    directory = tmp_path / "sessions" / reference.agent_id.value
    before = {
        p.relative_to(directory): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    }
    runner.fail = True
    with pytest.raises(InteractionFailure) as failure:
        run(adapter.turn(TurnRequest(reference, "failed")))
    assert failure.value.code is InteractionErrorCode.INFERENCE_UNAVAILABLE
    after = {
        p.relative_to(directory): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    }
    assert after == before
    runner.fail = False
    result = run(adapter.turn(TurnRequest(reference, "retry")))
    assert result.completed_turns == (2 if successful_first else 1)
    assert len({i.native_session_id for i in runner.invocations}) == 1
    assert "failed" not in runner.invocations[-1].prompt


def test_interrupted_commit_never_reopens_or_replaces_session(tmp_path: Path) -> None:
    runner = FakeQwenRunner()
    adapter = QwenSessionAdapter(config(tmp_path / "sessions"), runner=runner)
    reference = session()
    run(adapter.create_session(reference))
    directory = tmp_path / "sessions" / reference.agent_id.value
    (directory / ".turn-in-progress").write_text("pending\n")
    for operation in (
        adapter.create_session(reference),
        adapter.turn(TurnRequest(reference, "retry")),
    ):
        with pytest.raises(InteractionFailure) as failure:
            run(operation)
        assert failure.value.code is InteractionErrorCode.CORRUPT_STATE
    assert runner.invocations == []
    run(adapter.delete_session(reference))
    assert not directory.exists()


def test_injected_credential_is_redacted_in_conversation_artifacts(
    tmp_path: Path,
) -> None:
    runner = FakeQwenRunner(["synthetic-private-credential"])
    adapter = QwenSessionAdapter(
        config(tmp_path / "sessions", api_key="synthetic-private-credential"),
        runner=runner,
    )
    reference = session()
    run(adapter.create_session(reference))
    response = run(adapter.turn(TurnRequest(reference, "synthetic-private-credential")))
    assert response.response == "[REDACTED]"
    assert "synthetic-private-credential" not in runner.invocations[0].prompt
    for path in (tmp_path / "sessions").rglob("*"):
        if path.is_file():
            assert "synthetic-private-credential" not in path.read_text()


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
    assert captured.value.code is InteractionErrorCode.VALIDATION_FAILED
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


def test_task_mcp_configuration_uses_only_deployment_values(tmp_path: Path) -> None:
    reference = session()
    secret = "TASK012_TEST_TOKEN_MUST_NOT_PERSIST"
    adapter = QwenSessionAdapter(
        config(
            tmp_path / "sessions",
            task_api_base_url="http://host.docker.internal:8001",
            task_api_token=secret,
        ),
        runner=FakeQwenRunner(),
    )
    run(adapter.create_session(reference))

    directory = tmp_path / "sessions" / reference.agent_id.value
    settings = json.loads((directory / "qwen-home" / "settings.json").read_text())
    server = settings["mcpServers"]["task-rest"]
    assert settings["mcp"] == {"allowed": ["task-rest"]}
    assert server["includeTools"] == [
        "get_task",
        "create_task",
        "create_subtask",
        "update_task",
    ]
    assert server["args"] == ["/workspace/.uar-tools/task_rest_mcp_server.mjs"]
    assert (
        directory / "workspace" / ".uar-tools" / "task_rest_mcp_server.mjs"
    ).is_file()
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in directory.rglob("*")
        if path.is_file()
    )
    assert secret not in persisted
    run(adapter.close())


def test_qwen_command_allows_only_enabled_task_mcp_tools(tmp_path: Path) -> None:
    adapter_config = config(
        tmp_path / "sessions", task_api_base_url="http://host.docker.internal:8001"
    )
    runner = object.__new__(DockerQwenCommandRunner)
    runner._config = adapter_config
    invocation = QwenInvocation(
        tmp_path / "home",
        tmp_path / "workspace",
        uuid4(),
        "current message",
        False,
        ("get_task", "update_task"),
    )

    command = runner.command(invocation)
    assert command[command.index("--max-tool-calls") + 1] == "4"
    assert (
        command[command.index("--mcp-config") + 1] == "/root/.qwen/task-mcp-config.json"
    )
    assert command[command.index("--allowed-mcp-server-names") + 1] == "task-rest"
    allowed_index = command.index("--allowed-tools")
    assert command[allowed_index + 1 : allowed_index + 3] == [
        "task-rest__get_task",
        "task-rest__update_task",
    ]
    assert "create_task" not in command
    assert "create_subtask" not in command
    system_prompt = command[command.index("--system-prompt") + 1]
    assert "a text-only simulation is invalid" in system_prompt
    assert "copy the returned id, title, parent_id, and subtask_ids" in system_prompt


def test_qwen_command_uses_explicit_non_thinking_directive(tmp_path: Path) -> None:
    runner = object.__new__(DockerQwenCommandRunner)
    runner._config = config(tmp_path / "sessions", reasoning_directive="/no_think")
    invocation = QwenInvocation(
        tmp_path / "home", tmp_path / "workspace", uuid4(), "message", False
    )

    command = runner.command(invocation)

    assert command[command.index("-p") + 1].startswith("/no_think ")


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
