"""Conversation/session port, deliberately separate from runtime lifecycle."""

from typing import Protocol

from universal_agent_runtime.application.ports.interaction_values import (
    DeleteSessionResult,
    SessionObservation,
    SessionReference,
    TurnRequest,
    TurnResult,
)


class AgentInteraction(Protocol):
    """Operate one persistent logical conversation per Agent."""

    async def create_session(self, reference: SessionReference) -> SessionObservation:
        """Create or reopen the same logical Session without changing its turns."""
        ...

    async def turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn without silently replacing missing Session state."""
        ...

    async def delete_session(self, reference: SessionReference) -> DeleteSessionResult:
        """Remove all adapter-owned artifacts for the logical Session."""
        ...
