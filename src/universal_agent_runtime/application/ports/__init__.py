"""Application-owned contracts implemented by infrastructure adapters."""

from universal_agent_runtime.application.ports.agent_interaction import (
    AgentInteraction,
)
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

__all__ = [
    "AgentInteraction",
    "DeleteSessionResult",
    "InteractionErrorCode",
    "InteractionFailure",
    "InteractionOperation",
    "SessionObservation",
    "SessionReference",
    "TurnRequest",
    "TurnResult",
]
