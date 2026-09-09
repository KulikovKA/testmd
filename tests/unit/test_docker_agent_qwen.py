"""Docker interaction transport isolation and bounded native-state transfer."""

import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests.unit.test_qwen_session import config
from universal_agent_runtime.adapters.docker_agent_qwen import DockerAgentQwenRunner
from universal_agent_runtime.adapters.qwen_session import (
    QwenInvocation,
    QwenRunnerFailure,
)


def _runner(root: Path) -> DockerAgentQwenRunner:
    runner = object.__new__(DockerAgentQwenRunner)
    runner._config = config(root / "sessions")
    runner._workspace_target = "/workspace"
    runner._uid, runner._gid = 10001, 10001
    return runner


def _invocation(root: Path) -> QwenInvocation:
    home = root / "agent-one" / "qwen-home"
    home.mkdir(parents=True)
    (home / "settings.json").write_text("{}")
    return QwenInvocation(home, home.parent / "workspace", uuid4(), "hello", False)


@pytest.mark.parametrize(
    "name,kind",
    [
        (".qwen-home/../../escape", tarfile.REGTYPE),
        ("/absolute", tarfile.REGTYPE),
        ("foreign/file", tarfile.REGTYPE),
        (".qwen-home/link", tarfile.SYMTYPE),
        (".qwen-home/hard", tarfile.LNKTYPE),
        (".qwen-home/device", tarfile.CHRTYPE),
        (".qwen-home/..\\escape", tarfile.REGTYPE),
    ],
)
def test_archive_rejects_traversal_links_and_special_files(
    tmp_path: Path, name: str, kind: bytes
) -> None:
    invocation = _invocation(tmp_path)
    runner = _runner(tmp_path)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo(name)
        info.type = kind
        archive.addfile(info)
    with pytest.raises(QwenRunnerFailure):
        runner._receive(invocation, [stream.getvalue()])
    assert not (tmp_path / "escape").exists()


def test_native_archive_has_an_explicit_size_budget(tmp_path: Path) -> None:
    invocation = _invocation(tmp_path)
    runner = _runner(tmp_path)
    runner._config = config(tmp_path / "sessions", max_transcript_bytes=100)
    with pytest.raises(QwenRunnerFailure):
        runner._receive(invocation, [b"x" * 201])


def test_existing_agent_container_is_selected_and_home_is_non_root(
    tmp_path: Path,
) -> None:
    invocation = _invocation(tmp_path)
    runner = _runner(tmp_path)
    commands: list[list[str]] = []
    selection: list[dict[str, Any]] = []
    archive_bytes: list[bytes] = []

    class Container:
        status = "running"

        def exec_run(self, command: list[str], **kwargs: Any) -> Any:
            commands.append(command)
            if command[0] == "qwen":
                assert kwargs["workdir"] == "/workspace"
                return SimpleNamespace(
                    exit_code=0,
                    output=json.dumps(
                        {
                            "type": "result",
                            "subtype": "success",
                            "session_id": str(invocation.native_session_id),
                            "result": "OK",
                        }
                    ).encode(),
                )
            return SimpleNamespace(exit_code=0, output=b"")

        def put_archive(self, path: str, payload: bytes) -> bool:
            assert path == "/workspace"
            archive_bytes.append(payload)
            return True

        def get_archive(self, path: str) -> tuple[list[bytes], dict[str, Any]]:
            assert path == "/workspace/.qwen-home"
            return [archive_bytes[-1]], {}

    def select(**kwargs: Any) -> list[Container]:
        selection.append(kwargs)
        return [Container()]

    # No create/run method exists: executing a second runtime would fail this test.
    runner._client = SimpleNamespace(containers=SimpleNamespace(list=select))
    assert runner.run(invocation).response == "OK"
    assert selection[0]["filters"]["label"] == [
        "io.universal-agent-runtime.managed=true",
        "io.universal-agent-runtime.agent=agent-one",
    ]
    with tarfile.open(fileobj=io.BytesIO(archive_bytes[0])) as archive:
        assert archive.getmembers()[0].name.rstrip("/.") == ".qwen-home"
        assert all(member.uid == member.gid == 10001 for member in archive)
    assert commands[-1][0] == "qwen"


@pytest.mark.parametrize(
    "count,status", [(0, "running"), (2, "running"), (1, "exited")]
)
def test_ambiguous_or_stopped_runtime_is_never_replaced(
    tmp_path: Path, count: int, status: str
) -> None:
    runner = _runner(tmp_path)
    runner._client = SimpleNamespace(
        containers=SimpleNamespace(
            list=lambda **kwargs: [SimpleNamespace(status=status)] * count
        )
    )
    with pytest.raises(QwenRunnerFailure):
        runner.run(_invocation(tmp_path))
