"""Repository metadata API; native Git is a separate workspace concern."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from urllib.parse import urlsplit

from universal_agent_runtime.domain.development_task import (
    RepositoryTarget,
    validate_branch,
)
from universal_agent_runtime.domain.identifiers import validate_identifier


def validate_https_url(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(c) < 33 for c in value)
        or "\\" in value
    ):
        raise ValueError("invalid repository URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or ".." in parsed.path.split("/")
        or parsed.port == 0
    ):
        raise ValueError("invalid repository URL")
    return value


class CloneTransport(str, Enum):
    HTTPS = "https"
    LOCAL_TEST = "local_test"


@dataclass(frozen=True)
class RepositoryMetadata:
    repository_id: str
    namespace: str
    name: str
    default_branch: str = "main"
    web_url: str | None = None

    def __post_init__(self) -> None:
        RepositoryTarget(self.namespace, self.name, self.repository_id)
        validate_branch(self.default_branch)
        if self.web_url is not None:
            validate_https_url(self.web_url)


@dataclass(frozen=True)
class CloneInformation:
    repository_id: str
    location: str
    transport: CloneTransport = CloneTransport.HTTPS
    credential_reference: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.repository_id)
        if self.credential_reference is not None:
            validate_identifier(self.credential_reference)
        if not isinstance(self.transport, CloneTransport):
            raise ValueError("invalid clone transport")  # noqa: TRY004 - DTO validation contract
        if self.transport is CloneTransport.HTTPS:
            validate_https_url(self.location)
        elif (
            not isinstance(self.location, str)
            or not self.location
            or "\x00" in self.location
        ):
            raise ValueError("invalid local test location")


@dataclass(frozen=True)
class CreateRepositoryRequest:
    namespace: str
    name: str
    request_id: str
    default_branch: str = "main"

    def __post_init__(self) -> None:
        RepositoryTarget(self.namespace, self.name)
        validate_identifier(self.request_id)
        validate_branch(self.default_branch)


class RepositoryPlatformPort(Protocol):
    async def create_repository(
        self, request: CreateRepositoryRequest
    ) -> RepositoryMetadata: ...
    async def get_repository(self, repository_id: str) -> RepositoryMetadata: ...
    async def get_clone_information(self, repository_id: str) -> CloneInformation: ...
