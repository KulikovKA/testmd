"""Qwen transport inside the lifecycle-owned Docker Agent, never a second runtime."""

import io
import tarfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from docker.errors import DockerException

from universal_agent_runtime.adapters.qwen_session import (
    TASK_RESULT_LOG_PATH,
    TASK_TOOL_OPERATIONS,
    DockerQwenCommandRunner,
    QwenExecution,
    QwenInvocation,
    QwenRunnerFailure,
    QwenSessionConfig,
    _append_task_results,
    _classify_runner_output,
    _parse_qwen_output,
)
from universal_agent_runtime.adapters.qwen_session import (
    QwenRunnerErrorCode as Code,
)
from universal_agent_runtime.adapters.skill_packages import (
    SkillPackage,
    SkillPackageCatalog,
)
from universal_agent_runtime.domain.identifiers import AgentId


class DockerAgentQwenRunner(DockerQwenCommandRunner):
    """Synchronize adapter-owned native state into one managed workspace volume.

    The host copy is the recovery authority. Each invocation replaces the native
    home in the Agent volume, so a rolled-back failed turn cannot contaminate
    the next resume. Business workspace files are never replaced.
    """

    def __init__(
        self,
        config: QwenSessionConfig,
        *,
        workspace: str,
        user: str,
        skill_catalog: SkillPackageCatalog | None = None,
    ) -> None:
        super().__init__(config)
        self._workspace_target = workspace
        parts = user.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("Docker Qwen transport requires numeric uid:gid")
        self._uid, self._gid = map(int, parts)
        self._skill_catalog = skill_catalog or SkillPackageCatalog.builtins()

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
            configured_operations = self._task_operations(container)
            selected_skills = self._selected_skills(container, configured_operations)
            if selected_skills:
                selected_skills = tuple(
                    (
                        skill,
                        skill.authorized_tools(granted, invocation.current_message),
                    )
                    for skill, granted in selected_skills
                )
                task_operations = tuple(
                    operation
                    for operation in configured_operations
                    if any(operation in authorized for _, authorized in selected_skills)
                )
            else:
                task_operations = configured_operations
            invocation = replace(
                invocation,
                task_operations=task_operations,
                skill_instructions=tuple(
                    skill.prompt_fragment(granted) for skill, granted in selected_skills
                ),
            )
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
                paths: list[tuple[Path, str]] = [
                    (invocation.qwen_home, ".qwen-home"),
                    *(
                        (
                            path,
                            f".qwen-home/{path.relative_to(invocation.qwen_home).as_posix()}",
                        )
                        for path in sorted(invocation.qwen_home.rglob("*"))
                    ),
                ]
                tool_directory = invocation.workspace / ".uar-tools"
                if tool_directory.is_dir():
                    paths.extend(
                        [
                            (tool_directory, ".uar-tools"),
                            *(
                                (
                                    path,
                                    path.relative_to(invocation.workspace).as_posix(),
                                )
                                for path in sorted(tool_directory.rglob("*"))
                            ),
                        ]
                    )
                for skill, _ in selected_skills:
                    skill_root = skill.source_directory
                    paths.extend(
                        [
                            (skill_root, f".agent/skills/{skill.identifier}"),
                            *(
                                (
                                    path,
                                    (
                                        f".agent/skills/{skill.identifier}/"
                                        f"{path.relative_to(skill_root).as_posix()}"
                                    ),
                                )
                                for path in sorted(skill_root.rglob("*"))
                            ),
                        ]
                    )
                for path, archive_name in paths:
                    if path.is_symlink():
                        raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
                    info = bundle.gettarinfo(str(path), arcname=archive_name)
                    info.uid, info.gid = self._uid, self._gid
                    info.uname = info.gname = ""
                    if path.is_file():
                        with path.open("rb") as source:
                            bundle.addfile(info, source)
                    else:
                        bundle.addfile(info)
            if not container.put_archive(self._workspace_target, archive.getvalue()):
                raise QwenRunnerFailure(Code.OPERATION_FAILED)
            cleared_results = container.exec_run(
                [
                    "node",
                    "-e",
                    "const fs=require('fs');fs.rmSync(process.argv[1],{force:true});",
                    TASK_RESULT_LOG_PATH,
                ]
            )
            if cleared_results.exit_code != 0:
                raise QwenRunnerFailure(Code.OPERATION_FAILED)
            outcome = container.exec_run(
                self.command(invocation),
                workdir=self._workspace_target,
                environment={
                    "QWEN_HOME": home,
                    "HOME": home,
                    "OLLAMA_API_KEY": self._config.api_key,
                    "OPENAI_API_KEY": self._config.api_key,
                    "UAR_AGENT_TOOL_CAPABILITIES": ",".join(invocation.task_operations),
                    "UAR_TASK_RESULT_LOG": TASK_RESULT_LOG_PATH,
                    **self._task_environment(),
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
            execution = _append_task_results(
                execution,
                self._read_task_results(container),
                invocation.task_operations,
                self._config.task_api_max_response_bytes,
            )
            chunks, _ = container.get_archive(home)
            self._receive(invocation, chunks)
            return execution
        except QwenRunnerFailure:
            raise
        except (DockerException, OSError, ValueError, tarfile.TarError):
            raise QwenRunnerFailure(Code.OPERATION_FAILED) from None

    def _read_task_results(self, container: Any) -> bytes:
        limit = self._config.task_api_max_response_bytes * 4
        outcome = container.exec_run(
            [
                "node",
                "-e",
                (
                    "const fs=require('fs'),p=process.argv[1],m=Number(process.argv[2]);"
                    "try{const b=fs.readFileSync(p);fs.rmSync(p,{force:true});"
                    "if(b.length>m)process.exit(65);process.stdout.write(b)}"
                    "catch(e){if(e.code!=='ENOENT')process.exit(66)}"
                ),
                TASK_RESULT_LOG_PATH,
                str(limit),
            ]
        )
        if outcome.exit_code != 0 or not isinstance(outcome.output, bytes):
            raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
        return outcome.output

    def _environment_values(self, container: object) -> dict[str, str]:
        environment = getattr(container, "attrs", {}).get("Config", {}).get("Env", [])
        if not isinstance(environment, list):
            raise QwenRunnerFailure(Code.PROTOCOL_FAILURE)
        return {
            item.partition("=")[0]: item.partition("=")[2]
            for item in environment
            if isinstance(item, str) and "=" in item
        }

    def _task_operations(self, container: object) -> tuple[str, ...]:
        if self._config.task_api_base_url is None:
            return ()
        values = self._environment_values(container)
        return tuple(
            operation
            for operation in TASK_TOOL_OPERATIONS
            if operation in values.get("UAR_AGENT_TOOL_CAPABILITIES", "").split(",")
        )

    def _selected_skills(
        self, container: object, task_operations: tuple[str, ...] | None = None
    ) -> tuple[tuple[SkillPackage, tuple[str, ...]], ...]:
        selected = tuple(
            value
            for value in self._environment_values(container)
            .get("UAR_AGENT_SKILL_PACKAGES", "")
            .split(",")
            if value
        )
        return self._skill_catalog.resolve(
            selected,
            self._task_operations(container)
            if task_operations is None
            else task_operations,
        )

    def _task_environment(self) -> dict[str, str]:
        if self._config.task_api_base_url is None:
            return {}
        result = {
            "UAR_TASK_API_BASE_URL": self._config.task_api_base_url,
            "UAR_TASK_API_TIMEOUT_MS": str(
                round(self._config.task_api_timeout_seconds * 1000)
            ),
            "UAR_TASK_API_MAX_RESPONSE_BYTES": str(
                self._config.task_api_max_response_bytes
            ),
        }
        if self._config.task_api_token is not None:
            result["UAR_TASK_API_TOKEN"] = self._config.task_api_token
        return result

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
