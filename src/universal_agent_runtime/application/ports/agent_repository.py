"""Application-owned persistence port for Agent lifecycle metadata."""

from typing import TYPE_CHECKING, Protocol

from universal_agent_runtime.domain.identifiers import AgentId

if TYPE_CHECKING:
    from universal_agent_runtime.application.agent_lifecycle import AgentRecord


class AgentRepository(Protocol):
    """Store Agent records while retaining idempotency tombstones after delete."""

    def get(self, agent_id: AgentId) -> "AgentRecord | None": ...

    def owner_of_creation_request(self, request_id: str) -> AgentId | None: ...

    def add(self, record: "AgentRecord") -> None: ...

    def save(self, record: "AgentRecord") -> None: ...

    def delete(self, record: "AgentRecord") -> None: ...

    def was_deleted(self, agent_id: AgentId) -> bool: ...
