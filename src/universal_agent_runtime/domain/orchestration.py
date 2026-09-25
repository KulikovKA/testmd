"""Provider-neutral plan and status for high-level user work."""

import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum

from universal_agent_runtime.domain.identifiers import validate_identifier


class UserTaskStatus(str, Enum):
    CREATED = "created"
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerStatus(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class UserTask:
    user_task_id: str
    specification: str = field(repr=False)
    status: UserTaskStatus = UserTaskStatus.CREATED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    execution_graph_id: str | None = None
    result: str | None = None
    failure: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.user_task_id)
        if not isinstance(self.specification, str) or not self.specification.strip():
            raise ValueError("specification must be nonempty")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must have a timezone")
        if self.execution_graph_id is not None:
            validate_identifier(self.execution_graph_id)
        if not isinstance(self.status, UserTaskStatus):
            raise TypeError("invalid user task status")


@dataclass(frozen=True)
class WorkerResources:
    cpu_cores: float
    memory_bytes: int
    process_limit: int = 128

    def __post_init__(self) -> None:
        if (isinstance(self.cpu_cores, bool) or not isinstance(self.cpu_cores, (int, float))
                or not math.isfinite(self.cpu_cores) or self.cpu_cores <= 0):
            raise ValueError("cpu_cores must be finite and positive")
        if type(self.memory_bytes) is not int or self.memory_bytes <= 0:
            raise ValueError("memory_bytes must be positive")
        if type(self.process_limit) is not int or self.process_limit <= 0:
            raise ValueError("process_limit must be positive")


@dataclass(frozen=True)
class WorkerSpec:
    worker_id: str
    role: str
    goal: str = field(repr=False)
    dependencies: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    resources: WorkerResources = field(default_factory=lambda: WorkerResources(1, 1073741824))
    workspace_reference: str = ""
    status: WorkerStatus = WorkerStatus.WAITING
    result: str | None = None
    failure: str | None = None

    def __post_init__(self) -> None:
        for identifier in (self.worker_id, self.role, *self.dependencies, *self.skills):
            validate_identifier(identifier)
        validate_identifier(self.workspace_reference)
        if not isinstance(self.goal, str) or not self.goal.strip() or len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("invalid worker goal or dependencies")
        if self.worker_id in self.dependencies:
            raise ValueError("worker cannot depend on itself")
        if not isinstance(self.resources, WorkerResources):
            raise TypeError("invalid worker resources")
        if not isinstance(self.status, WorkerStatus):
            raise TypeError("invalid worker status")


class ExecutionGraph:
    def __init__(self, graph_id: str) -> None:
        validate_identifier(graph_id)
        self.graph_id = graph_id
        self._workers: dict[str, WorkerSpec] = {}

    @property
    def workers(self) -> tuple[WorkerSpec, ...]:
        return tuple(self._workers.values())

    def add_worker(self, worker: WorkerSpec) -> None:
        if worker.worker_id in self._workers:
            raise ValueError("duplicate worker")
        if any(dep not in self._workers for dep in worker.dependencies):
            raise ValueError("dependencies must be added before their worker")
        self._workers[worker.worker_id] = worker

    def add_dependency(self, worker_id: str, dependency_id: str) -> None:
        worker = self._workers[worker_id]
        if worker.status is not WorkerStatus.WAITING:
            raise ValueError("dependencies can only change before execution")
        if dependency_id not in self._workers or worker_id == dependency_id:
            raise ValueError("unknown or self dependency")
        if dependency_id in worker.dependencies:
            return
        def reaches(current: str) -> bool:
            return current == worker_id or any(reaches(d) for d in self._workers[current].dependencies)
        if reaches(dependency_id):
            raise ValueError("cyclic dependency")
        self._workers[worker_id] = replace(worker, dependencies=(*worker.dependencies, dependency_id))

    def set_status(self, worker_id: str, status: WorkerStatus, *, result: str | None = None,
                   failure: str | None = None) -> WorkerSpec:
        worker = self._workers[worker_id]
        allowed = {
            WorkerStatus.WAITING: {WorkerStatus.RUNNING},
            WorkerStatus.RUNNING: {WorkerStatus.SUSPENDED, WorkerStatus.COMPLETED, WorkerStatus.FAILED},
            WorkerStatus.SUSPENDED: {WorkerStatus.RUNNING, WorkerStatus.COMPLETED, WorkerStatus.FAILED},
        }
        if status not in allowed.get(worker.status, set()):
            raise ValueError("invalid worker status transition")
        if status is WorkerStatus.RUNNING and worker not in self.runnable_workers():
            if worker.status is not WorkerStatus.SUSPENDED:
                raise ValueError("worker is not runnable")
        if status in {WorkerStatus.COMPLETED, WorkerStatus.FAILED} and not (result if status is WorkerStatus.COMPLETED else failure):
            raise ValueError("terminal worker requires result or failure")
        updated = replace(worker, status=status, result=result, failure=failure)
        self._workers[worker_id] = updated
        self._propagate_failures()
        return updated

    def _propagate_failures(self) -> None:
        changed = True
        while changed:
            changed = False
            for worker in self._workers.values():
                if worker.status is WorkerStatus.WAITING and any(
                    self._workers[dep].status in {WorkerStatus.FAILED, WorkerStatus.BLOCKED}
                    for dep in worker.dependencies
                ):
                    self._workers[worker.worker_id] = replace(worker, status=WorkerStatus.BLOCKED,
                                                               failure="dependency_failed")
                    changed = True

    def runnable_workers(self) -> tuple[WorkerSpec, ...]:
        return tuple(w for w in self._workers.values() if w.status is WorkerStatus.WAITING
                     and all(self._workers[d].status is WorkerStatus.COMPLETED for d in w.dependencies))

    def completed_workers(self) -> tuple[WorkerSpec, ...]:
        return tuple(w for w in self._workers.values() if w.status is WorkerStatus.COMPLETED)

    def failed_workers(self) -> tuple[WorkerSpec, ...]:
        return tuple(w for w in self._workers.values() if w.status is WorkerStatus.FAILED)

    def blocked_workers(self) -> tuple[WorkerSpec, ...]:
        return tuple(w for w in self._workers.values() if w.status is WorkerStatus.BLOCKED)

    @property
    def completed(self) -> bool:
        return bool(self._workers) and all(w.status is WorkerStatus.COMPLETED for w in self._workers.values())

    @property
    def terminal(self) -> bool:
        return bool(self._workers) and all(w.status in {WorkerStatus.COMPLETED, WorkerStatus.FAILED,
                                                        WorkerStatus.BLOCKED} for w in self._workers.values())
