"""Closed workspace operations; there is deliberately no arbitrary shell port."""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from universal_agent_runtime.application.ports.repository_credentials import (
    RepositoryAccess,
)
from universal_agent_runtime.domain.development_task import BuildSystem, validate_branch
from universal_agent_runtime.domain.identifiers import AgentId, validate_identifier


def validate_project_path(path: str) -> str:
    if not isinstance(path, str) or not 1 <= len(path) <= 240:
        raise ValueError("invalid project path")
    parts = path.split("/")
    reserved = {
        ".git",
        ".gitmodules",
        ".gitattributes",
        ".env",
        ".agent",
        ".qwen-home",
        ".uar-tools",
        ".netrc",
        ".npmrc",
        "id_rsa",
        "id_ed25519",
    }
    if any(
        re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", p) is None
        or p in {".", ".."}
        or p.lower() in reserved
        or p.endswith((".", " "))
        or p.lower().split(".")[0]
        in {
            "con",
            "prn",
            "aux",
            "nul",
            *[f"com{i}" for i in range(1, 10)],
            *[f"lpt{i}" for i in range(1, 10)],
        }
        for p in parts
    ):
        raise ValueError("invalid project path")
    return path


@dataclass(frozen=True)
class ProjectFile:
    path: str
    content: str = field(repr=False)

    def __post_init__(self) -> None:
        validate_project_path(self.path)
        if (
            not isinstance(self.content, str)
            or "\x00" in self.content
            or len(self.content.encode()) > 32768
        ):
            raise ValueError("invalid project file")


class WorkspaceOperation(str, Enum):
    PREPARE = "prepare"
    INIT = "init"
    CLONE = "clone"
    WRITE = "write"
    INVENTORY = "inventory"
    STATUS = "status"
    DIFF = "diff"
    ADD = "add"
    COMMIT = "commit"
    PUSH = "push"
    TEST = "test"
    PACKAGE = "package"


@dataclass(frozen=True)
class WorkspaceRequest:
    task_id: str
    operation: WorkspaceOperation
    branch: str = "main"
    files: tuple[ProjectFile, ...] = ()
    paths: tuple[str, ...] = ()
    access: RepositoryAccess | None = None
    build_system: BuildSystem = BuildSystem.MAVEN

    def __post_init__(self) -> None:
        validate_identifier(self.task_id)
        validate_branch(self.branch)
        if not isinstance(self.operation, WorkspaceOperation) or not isinstance(
            self.build_system, BuildSystem
        ):
            raise ValueError("invalid operation")  # noqa: TRY004 - DTO validation contract
        if (
            not isinstance(self.files, tuple)
            or len(self.files) > 64
            or not all(isinstance(f, ProjectFile) for f in self.files)
        ):
            raise ValueError("invalid files")
        if len({f.path.lower() for f in self.files}) != len(self.files):
            raise ValueError("duplicate files")
        if not isinstance(self.paths, tuple) or len(self.paths) > 256:
            raise ValueError("invalid paths")
        for name in self.paths:
            validate_project_path(name)


@dataclass(frozen=True)
class WorkspaceResult:
    success: bool
    output: str = field(default="", repr=False)
    commit_id: str | None = None
    files: tuple[ProjectFile, ...] = ()
    check: str | None = None
    exit_code: int | None = None


class DevelopmentWorkspacePort(Protocol):
    execution_backend: str

    async def execute(
        self, agent_id: AgentId, request: WorkspaceRequest
    ) -> WorkspaceResult: ...
