"""Immutable, transport-neutral workflow facts; no model transcripts."""

import json
from dataclasses import dataclass, field

MAX_TRACE_EVENTS = 512
MAX_TRACE_BYTES = 512 * 1024
MAX_EVENT_BYTES = 16 * 1024
TRACE_KINDS = frozenset(
    {
        "phase",
        "llm_turn_started",
        "llm_turn_finished",
        "requirements_result",
        "plan_ready",
        "files_changed",
        "command_started",
        "command_finished",
        "review_result",
        "git_commit",
        "task_status",
    }
)
TRACE_PHASES = frozenset(
    {
        "requirements",
        "planning",
        "workspace",
        "repository",
        "implementation",
        "testing",
        "review",
        "fixing",
        "committing",
        "pushing",
        "task",
    }
)
TRACE_STATUS = frozenset({"started", "completed", "failed", "cancelled", "waiting"})


@dataclass(frozen=True)
class DevelopmentTraceEvent:
    sequence: int
    type: str
    phase: str
    status: str
    step_name: str | None
    attempt: int
    summary: str
    data_json: str = field(default="{}", repr=False)

    def to_dict(self) -> dict:
        return {
            "sequence": self.sequence,
            "type": self.type,
            "phase": self.phase,
            "status": self.status,
            "step_name": self.step_name,
            "attempt": self.attempt,
            "summary": self.summary,
            "data": json.loads(self.data_json),
        }

    def __post_init__(self) -> None:
        if (
            self.type not in TRACE_KINDS
            or self.phase not in TRACE_PHASES
            or self.status not in TRACE_STATUS
            or type(self.sequence) is not int
            or self.sequence < 1
            or type(self.attempt) is not int
            or not 0 <= self.attempt <= 512
            or len(self.summary) > 512
            or (
                self.step_name is not None
                and self.step_name != f"{self.phase}:{self.attempt}"
            )
        ):
            raise ValueError("invalid trace event")
        if (
            not isinstance(json.loads(self.data_json), dict)
            or len(json.dumps(self.to_dict(), ensure_ascii=False).encode())
            > MAX_EVENT_BYTES
        ):
            raise ValueError("trace event exceeds limits")
