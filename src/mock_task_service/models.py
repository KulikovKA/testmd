"""Internal Task model for the mock service; no corporate schema is implied."""

from dataclasses import dataclass, replace
from enum import Enum


class TaskStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.OPEN
    parent_id: str | None = None
    subtask_ids: tuple[str, ...] = ()
    version: int = 1

    def add_subtask(self, subtask_id: str) -> "Task":
        return replace(
            self,
            subtask_ids=(*self.subtask_ids, subtask_id),
            version=self.version + 1,
        )
