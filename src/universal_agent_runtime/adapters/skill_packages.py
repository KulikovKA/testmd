"""Trusted Skill package parsing and delivery data for the Qwen adapter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from universal_agent_runtime.application.ports.skill_store import (
    SkillDescriptor,
    SkillInstallRequest,
    SkillStoreFailure,
)
from universal_agent_runtime.domain.identifiers import validate_identifier

if TYPE_CHECKING:
    from universal_agent_runtime.adapters.filesystem_skill_registry import (
        FilesystemSkillRegistry,
    )

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_MANIFEST_FIELDS = {
    "schema_version",
    "id",
    "version",
    "summary",
    "instruction_file",
    "tool_capabilities",
    "mutation_tool_capabilities",
}
_EXPLICIT_TASK_CREATE_INTENT = re.compile(
    r"(?:\bcreate\b.{0,120}\btask(?:s)?\b|"
    r"\btask(?:s)?\b.{0,120}\bcreate\b|"
    r"\bсозда(?:й|йте|ть|дим|йте)\w*.{0,120}\bзадач\w*\b|"
    r"\bзадач\w*.{0,120}\bсозда(?:й|йте|ть|дим|йте)\w*\b)",
    re.IGNORECASE,
)
_EXPLICIT_EPIC_CREATE_INTENT = re.compile(
    r"(?:\bcreate\s+(?:an?\s+)?epics?\b|"
    r"\bepics?\b.{0,120}\bcreate\b|"
    r"\bсозда(?:й|йте|ть|дим)\w*.{0,120}\b(?:эпик\w*|epics?)\b|"
    r"\b(?:эпик\w*|epics?)\b.{0,120}\bсозда(?:й|йте|ть|дим)\w*)",
    re.IGNORECASE,
)
_EXPLICIT_ATTACH_INTENT = re.compile(
    r"(?:\b(?:attach|link)\b|\bпривяж\w*|\bприкреп\w*)", re.IGNORECASE
)
_DECOMPOSITION_INTENT = re.compile(
    r"(?:\bdecompos\w*|\bдекомпозир\w*|\bдочерн\w*)", re.IGNORECASE
)
_NEGATED_TASK_CREATE_INTENT = re.compile(
    r"(?:\b(?:do\s+not|don't)\s+create(?:\s+a\s+new)?\s+task\w*\b|"
    r"\bне\s+созда\w*(?:\s+нов\w*)?\s+задач\w*)",
    re.IGNORECASE,
)
_NEGATED_EPIC_CREATE_INTENT = re.compile(
    r"(?:\b(?:do\s+not|don't)\s+create(?:\s+an?)?\s+epics?\b|"
    r"\bне\s+созда\w*\s+(?:эпик\w*|epics?))",
    re.IGNORECASE,
)
_NEGATED_ATTACH_INTENT = re.compile(
    r"(?:\b(?:do\s+not|don't)\s+(?:attach|link)\b|\bне\s+привяж\w*)",
    re.IGNORECASE,
)


class SkillPackageError(ValueError):
    """The trusted package is malformed or cannot be selected."""


@dataclass(frozen=True)
class SkillPackage:
    identifier: str
    version: str
    summary: str
    instructions: str
    tool_capabilities: tuple[str, ...]
    mutation_tool_capabilities: tuple[str, ...]
    source_directory: Path

    def prompt_fragment(self, granted_tools: tuple[str, ...]) -> str:
        capabilities = ", ".join(granted_tools) or "none"
        return (
            f"Selected Skill: {self.identifier}@{self.version}.\n"
            f"Its immutable package is at .agent/skills/{self.identifier}.\n"
            f"Effective tool capabilities: {capabilities}.\n"
            "Follow its package workflow. The selection does not add tool capabilities. "
            "When an effective mutation capability is listed, execute the mutation "
            "explicitly requested in CURRENT_USER_MESSAGE with that tool; never simulate it.\n"
            "<SELECTED_SKILL_INSTRUCTIONS>\n"
            f"{self.instructions.rstrip()}\n"
            "</SELECTED_SKILL_INSTRUCTIONS>"
        )

    def authorized_tools(
        self, granted_tools: tuple[str, ...], current_message: str
    ) -> tuple[str, ...]:
        effective = tuple(
            tool for tool in granted_tools if tool in self.tool_capabilities
        )
        epic_requested = _EXPLICIT_EPIC_CREATE_INTENT.search(current_message) is not None
        decomposition_requested = _DECOMPOSITION_INTENT.search(current_message) is not None
        task_requested = (
            _EXPLICIT_TASK_CREATE_INTENT.search(current_message) is not None
            or (epic_requested and decomposition_requested)
        )
        attach_requested = (
            _EXPLICIT_ATTACH_INTENT.search(current_message) is not None
            or (decomposition_requested and (task_requested or epic_requested))
        )
        allowed_mutations = {
            "create_task": task_requested
            and _NEGATED_TASK_CREATE_INTENT.search(current_message) is None,
            "create_epic": epic_requested
            and _NEGATED_EPIC_CREATE_INTENT.search(current_message) is None,
            "add_child_task": attach_requested
            and _NEGATED_ATTACH_INTENT.search(current_message) is None,
        }
        return tuple(
            tool
            for tool in effective
            if tool not in self.mutation_tool_capabilities
            or allowed_mutations.get(tool, False)
        )


class SkillPackageCatalog:
    """Combine built-ins with current installed packages on every lookup."""

    def __init__(
        self,
        packages: tuple[SkillPackage, ...],
        registry: FilesystemSkillRegistry | None = None,
    ) -> None:
        by_identifier = {package.identifier: package for package in packages}
        if len(by_identifier) != len(packages):
            raise SkillPackageError("Skill package IDs must be unique")
        self._packages = by_identifier
        self._registry = registry

    @classmethod
    def builtins(cls) -> SkillPackageCatalog:
        """Discover every direct packaged Skill directory deterministically."""

        root = resources.files("universal_agent_runtime.agent_assets")
        package_roots = sorted(
            (
                Path(str(candidate))
                for candidate in root.iterdir()
                if candidate.is_dir() and candidate.joinpath("skill.json").is_file()
            ),
            key=lambda candidate: candidate.name,
        )
        return cls(tuple(load_skill_package(package_root) for package_root in package_roots))

    def with_registry(self, registry: FilesystemSkillRegistry) -> SkillPackageCatalog:
        return SkillPackageCatalog(tuple(self._packages.values()), registry)

    @staticmethod
    def _descriptor(package: SkillPackage, source_type: str) -> SkillDescriptor:
        return SkillDescriptor(
            package.identifier,
            package.version,
            package.summary,
            source_type,
            package.tool_capabilities,
            package.mutation_tool_capabilities,
        )

    def _lookup(self, identifier: str) -> SkillPackage | None:
        builtin = self._packages.get(identifier)
        installed = self._registry.get(identifier) if self._registry is not None else None
        if builtin is not None and installed is not None:
            raise SkillStoreFailure("skill_registry_unavailable")
        return builtin or installed

    def list_skills(self) -> tuple[SkillDescriptor, ...]:
        result = [self._descriptor(package, "builtin") for package in self._packages.values()]
        if self._registry is not None:
            for package in self._registry.packages():
                if package.identifier in self._packages:
                    raise SkillStoreFailure("skill_registry_unavailable")
                result.append(
                    self._descriptor(package, self._registry.source_type(package.identifier))
                )
        return tuple(sorted(result, key=lambda item: item.identifier))

    def get_skill(self, identifier: str) -> SkillDescriptor | None:
        package = self._lookup(identifier)
        if package is None:
            return None
        if identifier in self._packages:
            source_type = "builtin"
        else:
            assert self._registry is not None
            source_type = self._registry.source_type(identifier)
        return self._descriptor(package, source_type)

    def require_selected(self, identifiers: tuple[str, ...]) -> None:
        for identifier in identifiers:
            if self._lookup(identifier) is None:
                raise SkillStoreFailure("skill_unavailable")

    def install(self, request: SkillInstallRequest) -> SkillDescriptor:
        if self._registry is None:
            raise SkillStoreFailure("skill_source_unavailable")
        package = self._registry.install(request, frozenset(self._packages))
        return self._descriptor(package, request.source_type)

    def resolve(
        self, selected: tuple[str, ...], granted_tools: tuple[str, ...]
    ) -> tuple[tuple[SkillPackage, tuple[str, ...]], ...]:
        if len(selected) != len(set(selected)):
            raise SkillPackageError("Selected Skill IDs must be unique")
        result: list[tuple[SkillPackage, tuple[str, ...]]] = []
        for identifier in selected:
            package = self._lookup(identifier)
            if package is None:
                raise SkillPackageError("Selected Skill package is unavailable")
            effective = tuple(
                tool for tool in granted_tools if tool in package.tool_capabilities
            )
            result.append((package, effective))
        return tuple(result)


def load_skill_package(directory: Path) -> SkillPackage:
    """Parse one strict on-disk package without following asset links."""

    if not directory.is_dir() or directory.is_symlink():
        raise SkillPackageError("Skill package directory is invalid")
    manifest_path = directory / "skill.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise SkillPackageError("Skill manifest is missing")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SkillPackageError("Skill manifest is invalid") from error
    if not isinstance(raw, dict) or set(raw) != _MANIFEST_FIELDS:
        raise SkillPackageError("Skill manifest fields are invalid")
    if raw["schema_version"] != 1:
        raise SkillPackageError("Skill manifest schema is unsupported")
    identifier = raw["id"]
    version = raw["version"]
    summary = raw["summary"]
    instruction_file = raw["instruction_file"]
    tools = raw["tool_capabilities"]
    mutation_tools = raw["mutation_tool_capabilities"]
    try:
        if not isinstance(identifier, str):
            raise TypeError
        validate_identifier(identifier)
    except (TypeError, ValueError) as error:
        raise SkillPackageError("Skill ID is invalid") from error
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        raise SkillPackageError("Skill version must be semantic versioning")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 280:
        raise SkillPackageError("Skill summary is invalid")
    if instruction_file != "SKILL.md":
        raise SkillPackageError("Skill instruction file is invalid")
    if (
        not isinstance(tools, list)
        or len(tools) > 32
        or any(not isinstance(tool, str) for tool in tools)
        or len(tools) != len(set(tools))
    ):
        raise SkillPackageError("Skill tool capabilities are invalid")
    try:
        for tool in tools:
            validate_identifier(tool)
    except ValueError as error:
        raise SkillPackageError("Skill tool capability is invalid") from error
    if (
        not isinstance(mutation_tools, list)
        or any(not isinstance(tool, str) for tool in mutation_tools)
        or len(mutation_tools) != len(set(mutation_tools))
        or any(tool not in tools for tool in mutation_tools)
        or (not tools and mutation_tools)
    ):
        raise SkillPackageError("Skill mutation capabilities are invalid")
    instructions_path = directory / instruction_file
    if not instructions_path.is_file() or instructions_path.is_symlink():
        raise SkillPackageError("Skill instructions are missing")
    try:
        instructions = instructions_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SkillPackageError("Skill instructions are invalid") from error
    if not instructions.strip() or "\x00" in instructions or len(instructions) > 32_768:
        raise SkillPackageError("Skill instructions are invalid")
    return SkillPackage(
        identifier,
        version,
        summary,
        instructions,
        tuple(tools),
        tuple(mutation_tools),
        directory,
    )
