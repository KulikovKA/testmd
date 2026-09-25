"""AX v1alpha1 manifest projection and guarded transport seam.

This is not a gRPC implementation. AX currently has no Task field selecting a
Substrate sandbox class; placement must be verified by the deployment boundary.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from universal_agent_runtime.domain.identifiers import validate_identifier
from universal_agent_runtime.domain.orchestration import WorkerSpec, WorkerStatus


class AXUnavailable(RuntimeError):
    """Submission cannot prove the required worker isolation."""


@dataclass(frozen=True)
class AXConfiguration:
    atespace: str
    image: str
    command: tuple[str, ...]
    workspace_path: str = "/workspace"
    required_sandbox_class: str = "microvm"

    def __post_init__(self) -> None:
        validate_identifier(self.atespace)
        if not self.image or not self.command or not all(self.command):
            raise ValueError("AX worker image and command are required")
        if not self.workspace_path.startswith("/"):
            raise ValueError("AX workspace path must be absolute")
        if self.required_sandbox_class != "microvm":
            raise ValueError("AX Qwen workers require the microvm sandbox class")


def map_worker_to_ax_task(worker: WorkerSpec, config: AXConfiguration) -> dict:
    """Project only documented AX Task manifest fields; never include secrets."""
    if not worker.workspace_reference:
        raise ValueError("AX worker requires a dedicated workspace reference")
    return {
        "apiVersion": "ax.io/v1alpha1",
        "kind": "Task",
        "metadata": {"name": worker.worker_id, "atespace": config.atespace},
        "spec": {
            "image": config.image,
            "command": list(config.command),
            "resources": {"requests": {
                "cpu": str(worker.resources.cpu_cores),
                "memory": str(worker.resources.memory_bytes),
            }, "limits": {
                "cpu": str(worker.resources.cpu_cores),
                "memory": str(worker.resources.memory_bytes),
            }},
            "workspaces": [{"name": worker.workspace_reference, "path": config.workspace_path}],
            "debug": False,
        },
    }


class AXTransport(Protocol):
    """Future translation to the actual ax.v1alpha1.AX gRPC methods."""

    async def update_task(self, task_manifest: dict) -> None: ...
    async def get_task_phase(self, atespace: str, name: str) -> str: ...
    def watch_task_phase(self, atespace: str, name: str) -> AsyncIterator[str]: ...
    async def suspend_task(self, atespace: str, name: str) -> None: ...
    async def resume_task(self, atespace: str, name: str) -> None: ...
    async def delete_task(self, atespace: str, name: str) -> None: ...


class AXPlacementVerifier(Protocol):
    """Server-side evidence of selected Substrate class and worker policies."""

    async def verified_microvm(self, atespace: str, worker: WorkerSpec) -> bool: ...


class AXOrchestrator:
    def __init__(self, config: AXConfiguration, client: AXTransport | None = None,
                 verifier: AXPlacementVerifier | None = None) -> None:
        self.config, self.client, self.verifier = config, client, verifier

    async def submit(self, worker: WorkerSpec) -> WorkerStatus:
        manifest = map_worker_to_ax_task(worker, self.config)
        if self.client is None or self.verifier is None or not await self.verifier.verified_microvm(
            self.config.atespace, worker
        ):
            raise AXUnavailable("microvm placement is not verified")
        await self.client.update_task(manifest)
        return WorkerStatus.WAITING

    async def status(self, worker_id: str) -> WorkerStatus:
        if self.client is None:
            raise AXUnavailable("AX transport is not configured")
        phase = await self.client.get_task_phase(self.config.atespace, worker_id)
        return _phase(phase)

    async def watch(self, worker_id: str) -> AsyncIterator[WorkerStatus]:
        if self.client is None:
            raise AXUnavailable("AX transport is not configured")
        async for phase in self.client.watch_task_phase(self.config.atespace, worker_id):
            yield _phase(phase)

    async def suspend(self, worker_id: str) -> WorkerStatus:
        if self.client is None:
            raise AXUnavailable("AX transport is not configured")
        await self.client.suspend_task(self.config.atespace, worker_id)
        return await self.status(worker_id)

    async def resume(self, worker_id: str) -> WorkerStatus:
        if self.client is None:
            raise AXUnavailable("AX transport is not configured")
        await self.client.resume_task(self.config.atespace, worker_id)
        return await self.status(worker_id)

    async def delete(self, worker_id: str) -> None:
        if self.client is None:
            raise AXUnavailable("AX transport is not configured")
        await self.client.delete_task(self.config.atespace, worker_id)


def _phase(phase: str) -> WorkerStatus:
    mapping = {
        "Pending": WorkerStatus.WAITING,
        "Running": WorkerStatus.RUNNING,
        "Suspended": WorkerStatus.SUSPENDED,
        "Failed": WorkerStatus.FAILED,
    }
    if phase not in mapping:
        raise AXUnavailable("unknown AX Task phase")
    return mapping[phase]
