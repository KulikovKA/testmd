"""Trusted Skill package parsing and delivery data for the Qwen adapter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from universal_agent_runtime.domain.identifiers import validate_identifier

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
_EXPLICIT_CONFIRMATION = re.compile(
    r"\bexplicit(?:ly)?\s+confirm(?:ation|ed|ing)?\b|"
    r"\bявно\s+подтвержда(?:ю|ем|ете|ет)\b",
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
        if _EXPLICIT_CONFIRMATION.search(current_message) is not None:
            return effective
        return tuple(
            tool for tool in effective if tool not in self.mutation_tool_capabilities
        )


class SkillPackageCatalog:
    """Load immutable built-in assets; Skills never create Tool capabilities."""

    def __init__(self, packages: tuple[SkillPackage, ...]) -> None:
        by_identifier = {package.identifier: package for package in packages}
        if len(by_identifier) != len(packages):
            raise SkillPackageError("Skill package IDs must be unique")
        self._packages = by_identifier

    @classmethod
    def builtins(cls) -> SkillPackageCatalog:
        root = resources.files("universal_agent_runtime.agent_assets")
        package_root = Path(str(root.joinpath("task-decomposition")))
        return cls((load_skill_package(package_root),))

    def resolve(
        self, selected: tuple[str, ...], granted_tools: tuple[str, ...]
    ) -> tuple[tuple[SkillPackage, tuple[str, ...]], ...]:
        if len(selected) != len(set(selected)):
            raise SkillPackageError("Selected Skill IDs must be unique")
        result: list[tuple[SkillPackage, tuple[str, ...]]] = []
        for identifier in selected:
            package = self._packages.get(identifier)
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
        or not tools
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
