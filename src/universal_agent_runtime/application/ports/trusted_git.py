"""Credential-free application contract for existing repositories."""

import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from universal_agent_runtime.domain.development_task import (
    DevelopmentFailure,
    validate_branch,
)
from universal_agent_runtime.domain.identifiers import AgentId, validate_identifier


def validate_repository_url(
    value: str, endpoints: tuple[str, ...] | None = None
) -> str:
    try:
        if not isinstance(value, str) or len(value) > 1024:
            raise ValueError
        parsed = urlsplit(value)
        if (
            re.fullmatch(
                r"ssh://git@[a-z0-9][a-z0-9.-]*:[0-9]{1,5}/[A-Za-z0-9_./-]+", value
            )
            is None
            or parsed.scheme != "ssh"
            or parsed.username != "git"
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.port
            or not parsed.path.endswith(".git")
            or any(
                not p or p.startswith((".", "-")) or p.endswith(".")
                for p in parsed.path[1:].split("/")
            )
        ):
            raise ValueError
        endpoint = f"{parsed.hostname}:{parsed.port}"
        if endpoints is not None and endpoint not in endpoints:
            raise DevelopmentFailure("repository_not_allowed")
        return value
    except (ValueError, TypeError):
        raise DevelopmentFailure("repository_url_invalid") from None


@dataclass(frozen=True)
class GitRequest:
    task_id: str
    repository_url: str
    base_branch: str
    working_branch: str
    commit_id: str | None = None

    def __post_init__(self):
        validate_identifier(self.task_id)
        validate_repository_url(self.repository_url)
        validate_branch(self.base_branch)
        validate_branch(self.working_branch)
        if (
            self.working_branch != f"uar/{self.task_id}"
            or self.base_branch == self.working_branch
        ):
            raise DevelopmentFailure("repository_conflict")
        if (
            self.commit_id is not None
            and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.commit_id) is None
        ):
            raise DevelopmentFailure("repository_conflict")


class TrustedGitPort(Protocol):
    def validate(self, repository_url: str) -> None: ...
    async def clone(self, agent_id: AgentId, request: GitRequest) -> None: ...
    async def push(self, agent_id: AgentId, request: GitRequest) -> None: ...
