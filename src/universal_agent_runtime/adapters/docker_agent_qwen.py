"""Qwen transport inside the lifecycle-owned Docker Agent, never a second runtime."""

import io
import tarfile
from pathlib import PurePosixPath

from docker.errors import DockerException

from universal_agent_runtime.adapters.qwen_session import (
    DockerQwenCommandRunner,
    QwenExecution,
    QwenInvocation,
    QwenRunnerFailure,
    QwenSessionConfig,
    _classify_runner_output,
    _parse_qwen_output,
)
from universal_agent_runtime.adapters.qwen_session import (
    QwenRunnerErrorCode as Code,
)
from universal_agent_runtime.domain.identifiers import AgentId


class DockerAgentQwenRunner(DockerQwenCommandRunner):
    """Synchronize adapter-owned native state into one managed workspace volume.

    The host copy is the recovery authority. Each invocation replaces the native
    home in the Agent volume, so a rolled-back failed turn cannot contaminate
    the next resume. Business workspace files are never replaced.
    """

    def __init__(self, config: QwenSessionConfig, *, workspace: str, user: str) -> None:
        super().__init__(config)
        self._workspace_target = workspace
        parts = user.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("Docker Qwen transport requires numeric uid:gid")
        self._uid, self._gid = map(int, parts)

    def run(self, invocation: QwenInvocation) -> QwenExecution:
        agent_id = AgentId(invocation.workspace.parent.name)
        prefix = "io.universal-agent-runtime"
        home = f"{self._workspace_target}/.qwen-home"
        try:
            containers = self._client.containers.list(
                all=True,
                filters={
                    "label": [
                        f"{prefix}.managed=true",
                        f"{prefix}.agent={agent_id.value}",
                    ]
                },
            )
            if len(containers) != 1 or containers[0].status != "running":
                raise QwenRunnerFailure(Code.OPERATION_FAILED)
            container = containers[0]
            # Fixed adapter-owned path and argv; no shell or user-controlled command.
            cleared = container.exec_run(
                [
                    "node",
                    "-e",
                    "const fs=require('fs');fs.rmSync(process.argv[1],{recursive:true,force:true});",
                    home,
                ]
            )
            if cleared.exit_code != 0:
                raise QwenRunnerFailure(Code.OPERATION_FAILED)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as bundle:
                for path in [
                    invocation.qwen_home,
                    *sorted(invocation.qwen_home.rglob("*")),
                ]:
                    if path.is_symlink():
                        raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
                    relative = path.relative_to(invocation.qwen_home).as_posix()
                    info = bundle.gettarinfo(
                        str(path), arcname=f".qwen-home/{relative}"
                    )
                    info.uid, info.gid = self._uid, self._gid
                    info.uname = info.gname = ""
                    if path.is_file():
                        with path.open("rb") as source:
                            bundle.addfile(info, source)
                    else:
                        bundle.addfile(info)
            if not container.put_archive(self._workspace_target, archive.getvalue()):
                raise QwenRunnerFailure(Code.OPERATION_FAILED)
            outcome = container.exec_run(
                self.command(invocation),
                workdir=self._workspace_target,
                environment={
                    "QWEN_HOME": home,
                    "HOME": home,
                    "OLLAMA_API_KEY": self._config.api_key,
                    "OPENAI_API_KEY": self._config.api_key,
                },
            )
            if not isinstance(outcome.output, bytes):
                raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
            output = outcome.output.decode("utf-8", errors="replace")
            if outcome.exit_code == 55:
                raise QwenRunnerFailure(Code.TIMEOUT)
            if outcome.exit_code != 0:
                raise QwenRunnerFailure(_classify_runner_output(output))
            execution = _parse_qwen_output(output, invocation.native_session_id)
            chunks, _ = container.get_archive(home)
            self._receive(invocation, chunks)
            return execution
        except QwenRunnerFailure:
            raise
        except (DockerException, OSError, ValueError, tarfile.TarError):
            raise QwenRunnerFailure(Code.OPERATION_FAILED) from None

    def _receive(self, invocation: QwenInvocation, chunks: object) -> None:
        # Read a bounded archive and reject links, traversal, and special files.
        from collections.abc import Iterable

        if not isinstance(chunks, Iterable):
            raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
        payload = bytearray()
        for chunk in chunks:
            payload.extend(chunk)
            if len(payload) > self._config.max_transcript_bytes * 2:
                raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
        with tarfile.open(fileobj=io.BytesIO(payload)) as bundle:
            for member in bundle:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in member.name
                    or not path.parts
                    or path.parts[0] != ".qwen-home"
                    or not (member.isfile() or member.isdir())
                ):
                    raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
                target = invocation.qwen_home.joinpath(*path.parts[1:])
                if target.is_symlink() or not target.resolve().is_relative_to(
                    invocation.qwen_home.resolve()
                ):
                    raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    source = bundle.extractfile(member)
                    if (
                        source is None
                        or member.size > self._config.max_transcript_bytes
                    ):
                        raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read())
