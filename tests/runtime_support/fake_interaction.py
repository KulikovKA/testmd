"""Deterministic AgentInteraction double for lifecycle tests."""

from universal_agent_runtime.application.ports.agent_interaction import AgentInteraction
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
    InteractionFailure,
    InteractionOperation,
)
from universal_agent_runtime.application.ports.interaction_values import (
    DeleteSessionResult,
    SessionObservation,
    SessionReference,
    TurnRequest,
    TurnResult,
)


class FakeInteraction:
    def __init__(self) -> None:
        self.sessions: set[SessionReference] = set()
        self.create_calls = 0
        self.delete_calls = 0
        self._create_failure: InteractionErrorCode | None = None
        self._delete_failure: InteractionErrorCode | None = None
        self.turns: dict[SessionReference, list[str]] = {}
        self.turn_failure: InteractionErrorCode | None = None

    @property
    def interaction(self) -> AgentInteraction:
        return self

    def fail_create_once(self, code: InteractionErrorCode) -> None:
        self._create_failure = code

    def fail_delete_once(self, code: InteractionErrorCode) -> None:
        self._delete_failure = code

    async def create_session(self, reference: SessionReference) -> SessionObservation:
        self.create_calls += 1
        if self._create_failure is not None:
            code = self._create_failure
            self._create_failure = None
            raise InteractionFailure(InteractionOperation.CREATE, reference, code)
        self.sessions.add(reference)
        return SessionObservation(reference, 0)

    async def turn(self, request: TurnRequest) -> TurnResult:
        if request.session not in self.sessions:
            raise InteractionFailure(
                InteractionOperation.TURN,
                request.session,
                InteractionErrorCode.NOT_FOUND,
            )
        if self.turn_failure is not None:
            raise InteractionFailure(
                InteractionOperation.TURN, request.session, self.turn_failure
            )
        turns = self.turns.setdefault(request.session, [])
        turns.append(request.message)
        return TurnResult(request.session, len(turns), " | ".join(turns))

    async def delete_session(self, reference: SessionReference) -> DeleteSessionResult:
        self.delete_calls += 1
        if self._delete_failure is not None:
            code = self._delete_failure
            self._delete_failure = None
            raise InteractionFailure(InteractionOperation.DELETE, reference, code)
        self.sessions.discard(reference)
        self.turns.pop(reference, None)
        return DeleteSessionResult(reference)

    async def close(self) -> None:
        self.sessions.clear()
