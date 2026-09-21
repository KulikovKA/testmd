"""Conversation/session port, deliberately separate from runtime lifecycle."""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from universal_agent_runtime.application.ports.interaction_values import (
    AssistantTextDelta,
    DeleteSessionResult,
    SessionDebugSnapshot,
    SessionObservation,
    SessionReference,
    TurnRequest,
    TurnResult,
)
from universal_agent_runtime.application.ports.llm_turns import LLMTurnsPage


class AgentInteraction(Protocol):
    """Operate one persistent logical conversation per Agent."""

    async def create_session(self, reference: SessionReference) -> SessionObservation:
        """Create or reopen the same logical Session without changing its turns."""
        ...

    async def turn(self, request: TurnRequest) -> TurnResult:
        """Run one turn without silently replacing missing Session state.

        Callers serialize turns per Agent. Successful results commit exactly one
        consecutive turn. INFERENCE_UNAVAILABLE, TIMEOUT, VALIDATION_FAILED and
        TOOL_FAILED guarantee no committed turn and safe reuse of the same
        Session. Adapters must restore native state before raising these codes;
        indeterminate outcomes use a fatal state/protocol/operation failure.
        Tools are currently disabled; no external tool-write rollback is claimed.
        """
        ...

    async def delete_session(self, reference: SessionReference) -> DeleteSessionResult:
        """Remove all adapter-owned artifacts for the logical Session."""
        ...


@runtime_checkable
class AgentDebugReader(Protocol):
    """Optional read-only view of adapter-owned observability data."""

    async def debug_snapshot(
        self, reference: SessionReference
    ) -> SessionDebugSnapshot: ...


@runtime_checkable
class AgentLLMTurnsReader(Protocol):
    """Return only allowlisted, redacted committed turns; never raw native state."""

    async def llm_turns(
        self, reference: SessionReference, *, after: int = 0, limit: int = 10
    ) -> LLMTurnsPage: ...


@runtime_checkable
class StreamingAgentInteraction(Protocol):
    """Optional provisional text stream; final TurnResult still owns the commit.

    Emit only new, secret-redacted assistant text. Concatenated non-empty deltas
    must equal the final redacted response; a mismatch is a protocol failure.
    Adapters without partial output may return only the authoritative result.
    """

    async def turn_stream(
        self,
        request: TurnRequest,
        on_delta: Callable[[AssistantTextDelta], Awaitable[None]],
    ) -> TurnResult: ...
