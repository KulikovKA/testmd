"""Scoped repository access, never a raw credential value in a task or prompt."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from universal_agent_runtime.application.ports.repository_platform import (
    CloneInformation,
    CloneTransport,
)


class RepositoryAction(str, Enum):
    CLONE = "clone"
    PUSH = "push"


@dataclass(frozen=True)
class RepositoryAccess:
    repository_id: str
    location: str
    transport: CloneTransport
    branch: str
    action: RepositoryAction


class RepositoryCredentialPort(Protocol):
    def authorize(
        self,
        clone: CloneInformation,
        *,
        branch: str,
        action: RepositoryAction,
        publish_authorized: bool = False,
    ) -> RepositoryAccess: ...


class SecretPolicyPort(Protocol):
    def redact(self, text: str) -> str: ...
    def reject(self, text: str) -> None: ...
