"""Execute a closed command set in the lifecycle-owned Agent, never on the host."""

import asyncio
import json
from dataclasses import asdict
from typing import Any

from docker.errors import DockerException

from universal_agent_runtime.application.ports.development_workspace import (
    ProjectFile,
    WorkspaceOperation,
    WorkspaceRequest,
    WorkspaceResult,
)
from universal_agent_runtime.application.ports.repository_credentials import (
    RepositoryAction,
    SecretPolicyPort,
)
from universal_agent_runtime.application.ports.repository_platform import CloneTransport
from universal_agent_runtime.domain.development_task import DevelopmentFailure
from universal_agent_runtime.domain.identifiers import AgentId


def workspace_payload(request: WorkspaceRequest, secrets: SecretPolicyPort) -> dict:
    payload = {
        "task_id": request.task_id,
        "operation": request.operation.value,
        "branch": request.branch,
        "build_system": request.build_system.value,
        "files": [asdict(file) for file in request.files],
        "paths": list(request.paths),
    }
    for file in request.files:
        secrets.reject(file.path)
        secrets.reject(file.content)
    if request.operation in {WorkspaceOperation.CLONE, WorkspaceOperation.PUSH}:
        expected = (
            RepositoryAction.PUSH
            if request.operation is WorkspaceOperation.PUSH
            else RepositoryAction.CLONE
        )
        if (
            request.access is None
            or request.access.action is not expected
            or request.access.branch != request.branch
        ):
            raise DevelopmentFailure("publication_rejected")
        payload["remote"] = request.access.location
        payload["publish_authorized"] = expected is RepositoryAction.PUSH
    return payload


def workspace_result(raw: bytes, secrets: SecretPolicyPort) -> WorkspaceResult:
    if len(raw) > 262144:
        raise DevelopmentFailure("output_limit")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or type(value.get("success")) is not bool:
            raise ValueError
        if value.get("error"):
            raise DevelopmentFailure(value["error"])
        files = tuple(ProjectFile(**item) for item in value.get("files", []))
        for file in files:
            secrets.reject(file.path)
            secrets.reject(file.content)
        output = value.get("output", "")
        if not isinstance(output, str):
            raise TypeError
        exit_code = value.get("exit_code")
        if exit_code is not None and (
            type(exit_code) is not int
            or not 0 <= exit_code <= 255
            or value["success"] != (exit_code == 0)
        ):
            raise ValueError
        return WorkspaceResult(
            value["success"],
            secrets.redact(output),
            value.get("commit_id"),
            files,
            value.get("check"),
            exit_code,
        )
    except (TypeError, ValueError, UnicodeError):
        raise DevelopmentFailure("operation_failed") from None


class DockerDevelopmentWorkspaceAdapter:
    execution_backend = "agent"

    def __init__(
        self,
        client: Any,
        secrets: SecretPolicyPort,
        *,
        workspace: str = "/workspace",
        user: str = "10001:10001",
        allowed_hosts: tuple[str, ...] = (),
    ) -> None:
        if user != "10001:10001":
            raise ValueError(
                "development operations require the non-root Agent identity"
            )
        self._client, self._secrets = client, secrets
        self._workspace, self._user, self._allowed_hosts = (
            workspace,
            user,
            allowed_hosts,
        )

    def _execute(self, agent_id: AgentId, request: WorkspaceRequest) -> WorkspaceResult:
        if (
            request.access is not None
            and request.access.transport is not CloneTransport.HTTPS
        ):
            raise DevelopmentFailure("publication_rejected")
        payload = workspace_payload(request, self._secrets)
        serialized = json.dumps(payload, ensure_ascii=False)
        if len(serialized.encode()) > 131072:
            raise DevelopmentFailure("output_limit")
        try:
            containers = self._client.containers.list(
                all=True,
                filters={
                    "label": [
                        "io.universal-agent-runtime.managed=true",
                        f"io.universal-agent-runtime.agent={agent_id.value}",
                    ]
                },
            )
            if len(containers) != 1 or containers[0].status != "running":
                raise DevelopmentFailure("agent_unavailable")
            result = containers[0].exec_run(
                ["node", "/usr/local/lib/uar/workspace-operations.mjs", serialized],
                workdir=self._workspace,
                user=self._user,
                environment={
                    "UAR_WORKSPACE": self._workspace,
                    "UAR_GIT_ALLOWED_HOSTS": ",".join(self._allowed_hosts),
                    "OPENAI_API_KEY": "",
                    "OLLAMA_API_KEY": "",
                    "UAR_SFERA_USERNAME": "",
                    "UAR_SFERA_PASSWORD": "",
                },
                demux=True,
            )
            stdout, _stderr = result.output
            if request.operation is WorkspaceOperation.DIFF and stdout:
                self._secrets.reject(stdout.decode("utf-8"))
            decoded = workspace_result(stdout or b"{}", self._secrets)
            if result.exit_code != 0 and decoded.success:
                raise DevelopmentFailure("operation_failed")
            return decoded
        except DockerException:
            raise DevelopmentFailure("operation_failed") from None

    async def execute(
        self, agent_id: AgentId, request: WorkspaceRequest
    ) -> WorkspaceResult:
        # Helper enforces the deadline; wait for completion before releasing Agent.
        return await asyncio.to_thread(self._execute, agent_id, request)

    def close(self) -> None:
        self._client.close()
