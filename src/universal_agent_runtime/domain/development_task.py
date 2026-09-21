"""Provider-neutral Java development task and explicit transition policy."""

import re
from dataclasses import dataclass, field, replace
from enum import Enum

from universal_agent_runtime.domain.development_trace import DevelopmentTraceEvent
from universal_agent_runtime.domain.identifiers import AgentId, validate_identifier


class DevelopmentState(str, Enum):
    CREATED = "CREATED"
    ANALYZING_REQUIREMENTS = "ANALYZING_REQUIREMENTS"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    PLANNING = "PLANNING"
    PREPARING_WORKSPACE = "PREPARING_WORKSPACE"
    CREATING_REPOSITORY = "CREATING_REPOSITORY"
    IMPLEMENTING = "IMPLEMENTING"
    TESTING = "TESTING"
    REVIEWING = "REVIEWING"
    FIXING = "FIXING"
    COMMITTING = "COMMITTING"
    PUSHING = "PUSHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


S = DevelopmentState
TERMINAL_STATES = frozenset({S.COMPLETED, S.FAILED, S.CANCELLED})
TRANSITIONS = {
    S.CREATED: frozenset({S.ANALYZING_REQUIREMENTS}),
    S.ANALYZING_REQUIREMENTS: frozenset({S.WAITING_FOR_CLARIFICATION, S.PLANNING}),
    S.WAITING_FOR_CLARIFICATION: frozenset({S.ANALYZING_REQUIREMENTS}),
    S.PLANNING: frozenset({S.PREPARING_WORKSPACE}),
    S.PREPARING_WORKSPACE: frozenset({S.CREATING_REPOSITORY, S.IMPLEMENTING}),
    S.CREATING_REPOSITORY: frozenset({S.IMPLEMENTING}),
    S.IMPLEMENTING: frozenset({S.TESTING}),
    S.TESTING: frozenset({S.REVIEWING, S.FIXING}),
    S.REVIEWING: frozenset({S.FIXING, S.COMMITTING}),
    S.FIXING: frozenset({S.TESTING}),
    S.COMMITTING: frozenset({S.PUSHING, S.COMPLETED}),
    S.PUSHING: frozenset({S.COMPLETED}),
}


class BuildSystem(str, Enum):
    MAVEN = "maven"
    GRADLE = "gradle"


class DevelopmentFailure(Exception):
    """Only a stable allowlisted code crosses the application boundary."""

    CODES = frozenset(
        {
            "task_not_found",
            "task_conflict",
            "invalid_transition",
            "invalid_request",
            "agent_unavailable",
            "clarification_required",
            "invalid_model_result",
            "workspace_rejected",
            "operation_failed",
            "operation_timeout",
            "output_limit",
            "build_failed",
            "review_failed",
            "fix_limit",
            "repository_unavailable",
            "repository_conflict",
            "credential_unavailable",
            "publication_rejected",
            "cancelled",
            "secret_rejected",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self.CODES else "operation_failed"
        super().__init__(self.code)


def validate_branch(value: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", value) is None
        or ".." in value
        or "//" in value
        or any(
            part.startswith(".") or part.endswith((".", ".lock"))
            for part in value.split("/")
        )
        or value.endswith("/")
        or value == "HEAD"
    ):
        raise ValueError("invalid branch")
    return value


@dataclass(frozen=True)
class RepositoryTarget:
    namespace: str
    name: str
    repository_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or len(self.namespace) > 128:
            raise ValueError("invalid namespace")
        for part in self.namespace.split("/"):
            validate_identifier(part)
        validate_identifier(self.name)
        if self.repository_id is not None:
            validate_identifier(self.repository_id)


@dataclass(frozen=True)
class DevelopmentRequest:
    agent_id: AgentId
    specification: str = field(repr=False)
    build_system: BuildSystem = BuildSystem.MAVEN
    branch: str = "main"
    repository: RepositoryTarget | None = None
    publish: bool = False
    max_fix_attempts: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, AgentId) or not isinstance(
            self.build_system, BuildSystem
        ):
            raise ValueError("invalid development request")  # noqa: TRY004 - DTO validation contract
        if (
            not isinstance(self.specification, str)
            or not self.specification.strip()
            or len(self.specification) > 8000
            or "\x00" in self.specification
        ):
            raise ValueError("invalid specification")
        validate_branch(self.branch)
        if self.repository is not None and not isinstance(
            self.repository, RepositoryTarget
        ):
            raise ValueError("invalid repository target")
        if type(self.publish) is not bool or (self.publish and self.repository is None):
            raise ValueError("publication requires a repository")
        if (
            type(self.max_fix_attempts) is not int
            or not 0 <= self.max_fix_attempts <= 3
        ):
            raise ValueError("fix attempts must be in 0..3")


@dataclass(frozen=True)
class DevelopmentResult:
    branch: str
    commit_id: str
    files: tuple[str, ...]
    checks: tuple[str, ...]
    repository_id: str | None = None
    published: bool = False
    execution_backend: str = "test-double"

    def __post_init__(self) -> None:
        validate_branch(self.branch)
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.commit_id) is None:
            raise ValueError("invalid commit")
        if self.published and self.repository_id is None:
            raise ValueError("published result requires repository")


@dataclass(frozen=True)
class DevelopmentTask:
    task_id: str
    request: DevelopmentRequest
    state: DevelopmentState = S.CREATED
    version: int = 0
    transitions: tuple[DevelopmentState, ...] = (S.CREATED,)
    questions: tuple[str, ...] = ()
    clarification: str = field(default="", repr=False)
    plan: tuple[str, ...] = ()
    fix_attempts: int = 0
    result: DevelopmentResult | None = None
    failure_code: str | None = None
    cancel_requested: bool = False
    trace: tuple[DevelopmentTraceEvent, ...] = ()

    def __post_init__(self) -> None:
        validate_identifier(self.task_id)
        if not isinstance(self.request, DevelopmentRequest) or not isinstance(
            self.state, DevelopmentState
        ):
            raise ValueError("invalid task")  # noqa: TRY004 - DTO validation contract

    def transition(
        self, state: DevelopmentState, **changes: object
    ) -> "DevelopmentTask":
        if self.state in TERMINAL_STATES or (
            state not in {S.FAILED, S.CANCELLED}
            and state not in TRANSITIONS.get(self.state, ())
        ):
            raise DevelopmentFailure("invalid_transition")
        if state is S.FIXING:
            if self.fix_attempts >= self.request.max_fix_attempts:
                raise DevelopmentFailure("fix_limit")
            changes["fix_attempts"] = self.fix_attempts + 1
        if state is S.COMPLETED and not isinstance(
            changes.get("result"), DevelopmentResult
        ):
            raise DevelopmentFailure("invalid_transition")
        return replace(
            self,
            state=state,
            version=self.version + 1,
            transitions=(*self.transitions, state),
            **changes,
        )
