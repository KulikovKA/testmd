"""Deterministic repository API double; no HTTP, credentials or subprocesses."""

from hashlib import sha256
from pathlib import Path

from universal_agent_runtime.application.ports.repository_platform import (
    CloneInformation,
    CloneTransport,
    CreateRepositoryRequest,
    RepositoryMetadata,
)
from universal_agent_runtime.domain.development_task import DevelopmentFailure


class FakeRepositoryPlatformAdapter:
    def __init__(self, remote_root: Path) -> None:
        self.remote_root = remote_root.resolve()
        self._repositories: dict[str, RepositoryMetadata] = {}
        self._requests: dict[str, tuple[CreateRepositoryRequest, str]] = {}
        self._names: dict[tuple[str, str], str] = {}

    async def create_repository(
        self, request: CreateRepositoryRequest
    ) -> RepositoryMetadata:
        previous = self._requests.get(request.request_id)
        if previous is not None:
            if previous[0] != request:
                raise DevelopmentFailure("repository_conflict")
            return self._repositories[previous[1]]
        key = (request.namespace, request.name)
        if key in self._names:
            raise DevelopmentFailure("repository_conflict")
        identifier = sha256(
            (request.namespace + "/" + request.name).encode()
        ).hexdigest()[:24]
        metadata = RepositoryMetadata(
            identifier, request.namespace, request.name, request.default_branch
        )
        self._repositories[identifier] = metadata
        self._names[key] = identifier
        self._requests[request.request_id] = (request, identifier)
        return metadata

    async def get_repository(self, repository_id: str) -> RepositoryMetadata:
        metadata = self._repositories.get(repository_id)
        if metadata is None:
            raise DevelopmentFailure("repository_unavailable")
        return metadata

    async def get_clone_information(self, repository_id: str) -> CloneInformation:
        await self.get_repository(repository_id)
        return CloneInformation(
            repository_id,
            str(self.remote_root / f"{repository_id}.git"),
            CloneTransport.LOCAL_TEST,
        )
