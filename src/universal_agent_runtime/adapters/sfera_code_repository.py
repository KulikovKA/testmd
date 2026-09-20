"""Unconfigured boundary: no invented Sfera Code API or authentication."""

from universal_agent_runtime.application.ports.repository_platform import (
    CloneInformation,
    CreateRepositoryRequest,
    RepositoryMetadata,
)
from universal_agent_runtime.domain.development_task import DevelopmentFailure


class SferaCodeRepositoryAdapter:
    async def create_repository(
        self, request: CreateRepositoryRequest
    ) -> RepositoryMetadata:
        raise DevelopmentFailure("repository_unavailable")

    async def get_repository(self, repository_id: str) -> RepositoryMetadata:
        raise DevelopmentFailure("repository_unavailable")

    async def get_clone_information(self, repository_id: str) -> CloneInformation:
        raise DevelopmentFailure("repository_unavailable")
