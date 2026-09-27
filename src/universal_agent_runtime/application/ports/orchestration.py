"""Application boundaries for planning and worker infrastructure."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from universal_agent_runtime.domain.orchestration import ExecutionGraph, UserTask, WorkerSpec, WorkerStatus


class Coordinator(Protocol):
    def plan(self, task: UserTask) -> ExecutionGraph: ...


class WorkerOrchestrator(Protocol):
    async def submit(self, worker: WorkerSpec) -> WorkerStatus: ...
    async def status(self, worker_id: str) -> WorkerStatus: ...
    def watch(self, worker_id: str) -> AsyncIterator[WorkerStatus]: ...
    async def suspend(self, worker_id: str) -> WorkerStatus: ...
    async def resume(self, worker_id: str) -> WorkerStatus: ...
    async def delete(self, worker_id: str) -> None: ...


@dataclass(frozen=True)
class WorkerResult:
    """Command completion evidence, separate from infrastructure task status."""

    worker_id: str
    result: str | None = None
    failure: str | None = None

    def __post_init__(self) -> None:
        if not self.worker_id:
            raise ValueError("worker_id is required")
        if (self.result is None) == (self.failure is None):
            raise ValueError("exactly one of result or failure is required")
        if self.result is not None and not self.result.strip():
            raise ValueError("result must be nonempty")
        if self.failure is not None and not self.failure.strip():
            raise ValueError("failure must be nonempty")


class WorkerCompletionReader(Protocol):
    """Reads trusted command outcome; an AX Task phase alone is insufficient."""

    async def get_result(self, worker_id: str) -> WorkerResult: ...
