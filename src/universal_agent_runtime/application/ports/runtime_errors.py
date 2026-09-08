"""Safe, portable lifecycle failures; no backend exception text crosses the port."""

from enum import Enum

from universal_agent_runtime.application.ports.runtime_values import RuntimeHandle
from universal_agent_runtime.domain.identifiers import AgentId


class RuntimeOperation(str, Enum):
    CREATE = "create"
    START = "start"
    STATUS = "status"
    STOP = "stop"
    DELETE = "delete"


class RuntimeErrorCode(str, Enum):
    CONFIGURATION_REJECTED = "configuration_rejected"
    NOT_FOUND = "not_found"
    INVALID_STATE = "invalid_state"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CLEANUP_FAILED = "cleanup_failed"
    OPERATION_FAILED = "operation_failed"


class RuntimeFailure(Exception):
    """Stable category, operation and recovery reference without arbitrary detail.

    Retryable means reconcile and retry the same identity, never allocate a new one.
    Adapters must suppress raw chained exceptions at their trust boundary.
    """

    def __init__(
        self,
        operation: RuntimeOperation,
        agent_id: AgentId,
        code: RuntimeErrorCode,
        handle: RuntimeHandle | None = None,
    ) -> None:
        if (
            not isinstance(operation, RuntimeOperation)
            or not isinstance(code, RuntimeErrorCode)
            or not isinstance(agent_id, AgentId)
        ):
            raise TypeError("invalid runtime failure")
        if handle is not None and (
            not isinstance(handle, RuntimeHandle) or handle.agent_id != agent_id
        ):
            raise ValueError("failure handle must belong to the same Agent")
        self.operation = operation
        self.agent_id = agent_id
        self.code = code
        self.handle = handle
        self.retryable = code in {
            RuntimeErrorCode.UNAVAILABLE,
            RuntimeErrorCode.TIMEOUT,
            RuntimeErrorCode.CLEANUP_FAILED,
        }
        super().__init__(f"{operation.value}: {code.value} (agent={agent_id.value})")
