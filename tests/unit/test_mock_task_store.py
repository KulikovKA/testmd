"""Deterministic storage and configuration tests for the mock service."""

import pytest

from mock_task_service.config import Settings
from mock_task_service.models import TaskStatus
from mock_task_service.store import (
    InMemoryTaskStore,
    TaskConflictError,
    TaskNotFoundError,
)


def test_store_operations_are_deterministic() -> None:
    store = InMemoryTaskStore()
    parent = store.create(title="Parent", description="")
    child = store.create(title="Child", description="", parent_id=parent.id)
    updated = store.update(
        child.id,
        expected_version=1,
        title=None,
        description=None,
        status=TaskStatus.DONE,
    )

    assert (parent.id, child.id) == ("task-0001", "task-0002")
    assert store.get(parent.id).subtask_ids == (child.id,)
    assert store.get(parent.id).version == 2
    assert updated.status is TaskStatus.DONE
    assert updated.version == 2
    with pytest.raises(TaskConflictError):
        store.update(
            child.id,
            expected_version=1,
            title="Stale",
            description=None,
            status=None,
        )
    with pytest.raises(TaskNotFoundError):
        store.get("task-9999")


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_invalid_config_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MOCK_TASK_API_PORT", value)
    with pytest.raises(ValueError):
        Settings.from_environment()


def test_config_reads_explicit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOCK_TASK_API_HOST", "127.0.0.2")
    monkeypatch.setenv("MOCK_TASK_API_PORT", "8100")
    assert Settings.from_environment() == Settings(host="127.0.0.2", port=8100)
