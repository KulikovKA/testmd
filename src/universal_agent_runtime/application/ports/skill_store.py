"""Runtime neutral metadata and operations for managed Skill packages."""

from dataclasses import dataclass
from typing import Protocol

MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 256


@dataclass(frozen=True)
class SkillDescriptor:
    identifier: str
    version: str
    summary: str
    source_type: str
    tool_capabilities: tuple[str, ...]
    mutation_tool_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class SkillInstallRequest:
    source_type: str
    archive: bytes | None = None
    repository_url: str | None = None
    revision: str | None = None
    path: str | None = None


class SkillStoreFailure(Exception):
    """A stable public failure, with no paths or backend exception details."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SkillStore(Protocol):
    def list_skills(self) -> tuple[SkillDescriptor, ...]: ...

    def get_skill(self, identifier: str) -> SkillDescriptor | None: ...

    def require_selected(self, identifiers: tuple[str, ...]) -> None: ...

    def install(self, request: SkillInstallRequest) -> SkillDescriptor: ...
