"""Thread-safe process-local state for deterministic mock tests and development."""

from dataclasses import replace
from threading import Lock

from mock_task_service.models import Task, TaskStatus


class TaskNotFoundError(Exception):
    pass


class TaskConflictError(Exception):
    pass


class InMemoryTaskStore:
    """One isolated state container; instantiate it per app/test."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._next_id = 1
        self._lock = Lock()

    def _allocate_id(self) -> str:
        task_id = f"task-{self._next_id:04d}"
        self._next_id += 1
        return task_id

    def get(self, task_id: str) -> Task:
        with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError:
                raise TaskNotFoundError from None

    def create(
        self, *, title: str, description: str, parent_id: str | None = None
    ) -> Task:
        with self._lock:
            parent = None
            if parent_id is not None:
                parent = self._tasks.get(parent_id)
                if parent is None:
                    raise TaskNotFoundError

            task = Task(
                id=self._allocate_id(),
                title=title,
                description=description,
                parent_id=parent_id,
            )
            self._tasks[task.id] = task
            if parent is not None:
                self._tasks[parent.id] = parent.add_subtask(task.id)
            return task

    def update(
        self,
        task_id: str,
        *,
        expected_version: int,
        title: str | None,
        description: str | None,
        status: TaskStatus | None,
    ) -> Task:
        with self._lock:
            current = self._tasks.get(task_id)
            if current is None:
                raise TaskNotFoundError
            if current.version != expected_version:
                raise TaskConflictError
            updated = replace(
                current,
                title=current.title if title is None else title,
                description=current.description if description is None else description,
                status=current.status if status is None else status,
                version=current.version + 1,
            )
            self._tasks[task_id] = updated
            return updated
