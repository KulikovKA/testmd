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


class AXAdmissionPolicyVerifier(Protocol):
    """Check deployment policy/configuration before creating an AX Task."""

    async def verify_admission_policy(self, atespace: str, worker: WorkerSpec) -> bool: ...


class AXTaskActorAttestor(Protocol):
    """Attest actual AX Task to Substrate Actor and microVM after launch."""

    async def attest_task_actor_microvm(self, atespace: str, task_name: str) -> bool: ...


class AXWorkspaceRegistry(Protocol):
    """Confirm a provisioned AX Workspace exists before a Task can reference it."""

    async def workspace_is_ready(self, atespace: str, workspace_name: str) -> bool: ...


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


class AXOrchestrator:
    def __init__(self, config: AXConfiguration, client: AXTransport | None = None,
                 admission_verifier: AXAdmissionPolicyVerifier | None = None,
                 placement_attestor: AXTaskActorAttestor | None = None,
                 workspaces: AXWorkspaceRegistry | None = None) -> None:
        self.config = config
        self.client = client
        self.admission_verifier = admission_verifier
        self.placement_attestor = placement_attestor
        self.workspaces = workspaces

    async def verify_pre_admission(self, worker: WorkerSpec) -> None:
        """Check policy and workspace readiness; this does not prove placement."""
        if self.admission_verifier is None or not await self.admission_verifier.verify_admission_policy(
            self.config.atespace, worker
        ):
            raise AXUnavailable("AX admission policy/configuration is not verified")
        if self.workspaces is None or not await self.workspaces.workspace_is_ready(
            self.config.atespace, worker.workspace_reference
        ):
            raise AXUnavailable("referenced AX Workspace is not provisioned and ready")

    async def attest_post_launch(self, task_name: str) -> None:
        """Check actual placement after launch; a pre-admission check is not attestation."""
        validate_identifier(task_name)
        if self.placement_attestor is None or not await self.placement_attestor.attest_task_actor_microvm(
            self.config.atespace, task_name
        ):
            raise AXUnavailable("actual AX Task/Substrate Actor microvm placement is not attested")

    async def submit(self, worker: WorkerSpec) -> WorkerStatus:
        map_worker_to_ax_task(worker, self.config)
        await self.verify_pre_admission(worker)
        if self.placement_attestor is None:
            raise AXUnavailable("post-launch Task/Actor microvm attestation is not configured")
        # Current AX Task submission starts the runner without an established
        # hold-before-execution gate. It is unsafe to submit and attest afterward.
        # Keep the transport untouched until AX/Substrate can provide that gate.
        raise AXUnavailable("AX cannot gate worker execution pending post-launch attestation")

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
