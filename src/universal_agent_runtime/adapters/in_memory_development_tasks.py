"""Bounded process-local task storage with optimistic version checks."""

from universal_agent_runtime.domain.development_task import (
    DevelopmentFailure,
    DevelopmentTask,
)


class InMemoryDevelopmentTaskRepository:
    def __init__(self, *, capacity: int = 1024) -> None:
        self._tasks: dict[str, DevelopmentTask] = {}
        self._capacity = capacity

    def get(self, task_id: str) -> DevelopmentTask | None:
        return self._tasks.get(task_id)

    def add(self, task: DevelopmentTask) -> None:
        if task.task_id in self._tasks or len(self._tasks) >= self._capacity:
            raise DevelopmentFailure("task_conflict")
        self._tasks[task.task_id] = task

    def save(self, task: DevelopmentTask, *, expected_version: int) -> None:
        current = self._tasks.get(task.task_id)
        if (
            current is None
            or current.version != expected_version
            or task.version != expected_version + 1
        ):
            raise DevelopmentFailure("task_conflict")
        if task.request != current.request:
            raise DevelopmentFailure("task_conflict")
        self._tasks[task.task_id] = task
