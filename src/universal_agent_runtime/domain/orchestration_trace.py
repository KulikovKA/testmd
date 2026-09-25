"""High-level events; development turn traces remain separate."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

from universal_agent_runtime.domain.identifiers import validate_identifier


class OrchestrationEventKind(str, Enum):
    USER_TASK_CREATED = "USER_TASK_CREATED"
    PLAN_CREATED = "PLAN_CREATED"
    WORKER_CREATED = "WORKER_CREATED"
    WORKER_WAITING = "WORKER_WAITING"
    WORKER_RUNNING = "WORKER_RUNNING"
    WORKER_COMPLETED = "WORKER_COMPLETED"
    WORKER_FAILED = "WORKER_FAILED"
    WORKER_RETRYING = "WORKER_RETRYING"
    WORKER_SUSPENDED = "WORKER_SUSPENDED"
    WORKER_RESUMED = "WORKER_RESUMED"
    AGGREGATION_STARTED = "AGGREGATION_STARTED"
    USER_TASK_COMPLETED = "USER_TASK_COMPLETED"
    USER_TASK_FAILED = "USER_TASK_FAILED"


@dataclass(frozen=True)
class OrchestrationTraceEvent:
    sequence: int
    user_task_id: str
    kind: OrchestrationEventKind
    worker_id: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("invalid trace sequence")
        validate_identifier(self.user_task_id)
        if self.worker_id is not None:
            validate_identifier(self.worker_id)
        if self.occurred_at.tzinfo is None:
            raise ValueError("event time must have a timezone")


class OrchestrationTrace:
    """Small in-memory trace value; persistence belongs to a later repository."""

    def __init__(self, user_task_id: str, *, max_events: int = 512) -> None:
        validate_identifier(user_task_id)
        if type(max_events) is not int or max_events < 1:
            raise ValueError("max_events must be positive")
        self.user_task_id = user_task_id
        self.max_events = max_events
        self._events: list[OrchestrationTraceEvent] = []

    @property
    def events(self) -> tuple[OrchestrationTraceEvent, ...]:
        return tuple(self._events)

    def record(self, kind: OrchestrationEventKind, worker_id: str | None = None) -> OrchestrationTraceEvent:
        if len(self._events) >= self.max_events:
            raise ValueError("orchestration trace limit reached")
        event = OrchestrationTraceEvent(len(self._events) + 1, self.user_task_id, kind, worker_id)
        self._events.append(event)
        return event
