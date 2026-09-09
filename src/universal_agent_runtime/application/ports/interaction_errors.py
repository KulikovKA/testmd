"""Stable conversation failures without Qwen, process, or filesystem details."""

from enum import Enum

from universal_agent_runtime.application.ports.interaction_values import (
    SessionReference,
)


class InteractionOperation(str, Enum):
    CREATE = "create"
    TURN = "turn"
    DELETE = "delete"


class InteractionErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    CORRUPT_STATE = "corrupt_state"
    INCOMPATIBLE_STATE = "incompatible_state"
    INFERENCE_UNAVAILABLE = "inference_unavailable"
    TIMEOUT = "timeout"
    PROTOCOL_FAILURE = "protocol_failure"
    OPERATION_FAILED = "operation_failed"
    CLEANUP_FAILED = "cleanup_failed"


class InteractionFailure(Exception):
    """Portable failure category with no raw model, SDK, or secret detail."""

    def __init__(
        self,
        operation: InteractionOperation,
        session: SessionReference,
        code: InteractionErrorCode,
    ) -> None:
        if not isinstance(operation, InteractionOperation) or not isinstance(
            code, InteractionErrorCode
        ):
            raise TypeError("invalid interaction failure")
        if not isinstance(session, SessionReference):
            raise TypeError("invalid Session reference")
        self.operation = operation
        self.session = session
        self.code = code
        self.retryable = code in {
            InteractionErrorCode.INFERENCE_UNAVAILABLE,
            InteractionErrorCode.TIMEOUT,
            InteractionErrorCode.OPERATION_FAILED,
            InteractionErrorCode.CLEANUP_FAILED,
        }
        super().__init__(
            f"{operation.value}: {code.value} "
            f"(agent={session.agent_id.value}, session={session.session_id.value})"
        )
