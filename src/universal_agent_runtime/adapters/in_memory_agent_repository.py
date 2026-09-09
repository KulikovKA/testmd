"""Explicit process-local Agent metadata adapter for the PoC."""

from universal_agent_runtime.application.agent_lifecycle import AgentRecord
from universal_agent_runtime.application.ports.agent_repository import AgentRepository
from universal_agent_runtime.domain.identifiers import AgentId


class InMemoryAgentRepository:
    """Application-lifetime records and tombstones; no hidden global state."""

    def __init__(self) -> None:
        self._records: dict[AgentId, AgentRecord] = {}
        self._creation_owners: dict[str, AgentId] = {}
        self._deleted: set[AgentId] = set()

    @property
    def repository(self) -> AgentRepository:
        return self

    def get(self, agent_id: AgentId) -> AgentRecord | None:
        return self._records.get(agent_id)

    def owner_of_creation_request(self, request_id: str) -> AgentId | None:
        return self._creation_owners.get(request_id)

    def add(self, record: AgentRecord) -> None:
        if (
            record.agent_id in self._records
            or record.agent_id in self._deleted
            or record.creation_request_id in self._creation_owners
        ):
            raise ValueError("Agent identity or creation request already exists")
        self._records[record.agent_id] = record
        self._creation_owners[record.creation_request_id] = record.agent_id

    def save(self, record: AgentRecord) -> None:
        current = self._records.get(record.agent_id)
        if current is None or current.creation_request_id != record.creation_request_id:
            raise ValueError("cannot save an unknown Agent record")
        self._records[record.agent_id] = record

    def delete(self, record: AgentRecord) -> None:
        current = self._records.get(record.agent_id)
        if current is None or current.creation_request_id != record.creation_request_id:
            raise ValueError("cannot delete an unknown Agent record")
        del self._records[record.agent_id]
        self._deleted.add(record.agent_id)

    def was_deleted(self, agent_id: AgentId) -> bool:
        return agent_id in self._deleted
