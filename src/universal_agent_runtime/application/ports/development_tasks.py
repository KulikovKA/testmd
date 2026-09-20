"""Persistence for development tasks, independent of repository hosting."""

from typing import Protocol

from universal_agent_runtime.domain.development_task import DevelopmentTask


class DevelopmentTaskRepository(Protocol):
    def get(self, task_id: str) -> DevelopmentTask | None: ...
    def add(self, task: DevelopmentTask) -> None: ...
    def save(self, task: DevelopmentTask, *, expected_version: int) -> None: ...
