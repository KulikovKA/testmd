"""Persistent, append-only Skill registry on a deployment-owned filesystem."""

import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path

from universal_agent_runtime.adapters.skill_packages import (
    SkillPackage,
    SkillPackageError,
    load_skill_package,
)
from universal_agent_runtime.adapters.skill_sources import (
    MAX_ARCHIVE_ENTRIES,
    MAX_UNCOMPRESSED_BYTES,
    ArchiveSkillSource,
    GitSkillSource,
    SkillSource,
)
from universal_agent_runtime.application.ports.skill_store import (
    SkillInstallRequest,
    SkillStoreFailure,
)
from universal_agent_runtime.domain.identifiers import validate_identifier

_ORIGIN_FILE = ".uar-source.json"


class FilesystemSkillRegistry:
    """Install validated packages by sibling rename; never replace an installed ID."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("Skill registry root must be absolute and non-root")
        try:
            root.mkdir(parents=True, exist_ok=True)
            if root.is_symlink() or not root.is_dir() or root.resolve() != root:
                raise ValueError("Skill registry root is invalid")
        except OSError:
            raise ValueError("Skill registry root is unavailable") from None
        self.root = root
        self._sources: dict[str, SkillSource] = {
            "archive": ArchiveSkillSource(),
            "git": GitSkillSource(),
        }

    def _load(self, directory: Path) -> SkillPackage:
        try:
            if directory.is_symlink():
                raise ValueError
            paths = tuple(directory.rglob("*"))
            if (
                len(paths) > MAX_ARCHIVE_ENTRIES + 1
                or sum(
                    path.stat().st_size
                    for path in paths
                    if path.is_file() and not path.is_symlink()
                )
                > MAX_UNCOMPRESSED_BYTES
            ):
                raise ValueError
            if any(
                path.is_symlink() or not (path.is_file() or path.is_dir())
                for path in paths
            ):
                raise ValueError
            if any(
                not path.resolve().is_relative_to(directory.resolve()) for path in paths
            ):
                raise ValueError
            package = load_skill_package(directory)
            if package.identifier != directory.name:
                raise ValueError
            return package
        except (OSError, ValueError, SkillPackageError):
            raise SkillStoreFailure("skill_registry_unavailable") from None

    def packages(self) -> tuple[SkillPackage, ...]:
        try:
            directories = sorted(
                (
                    entry
                    for entry in self.root.iterdir()
                    if entry.is_dir() and not entry.name.startswith(".")
                ),
                key=lambda entry: entry.name,
            )
        except OSError:
            raise SkillStoreFailure("skill_registry_unavailable") from None
        return tuple(self._load(directory) for directory in directories)

    def get(self, identifier: str) -> SkillPackage | None:
        validate_identifier(identifier)
        directory = self.root / identifier
        if directory.is_symlink():
            raise SkillStoreFailure("skill_registry_unavailable")
        if not directory.exists():
            return None
        if not directory.is_dir():
            raise SkillStoreFailure("skill_registry_unavailable")
        return self._load(directory)

    def source_type(self, identifier: str) -> str:
        marker = self.root / identifier / _ORIGIN_FILE
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise SkillStoreFailure("skill_registry_unavailable") from None
        if value not in self._sources:
            raise SkillStoreFailure("skill_registry_unavailable")
        return value

    def install(
        self, request: SkillInstallRequest, builtin_ids: frozenset[str]
    ) -> SkillPackage:
        source = self._sources.get(request.source_type)
        if source is None:
            raise SkillStoreFailure("skill_request_invalid")
        try:
            with tempfile.TemporaryDirectory(
                prefix=".skill-stage-", dir=self.root
            ) as staging_name:
                staging = Path(staging_name)
                directory = source.materialize(request, staging)
                try:
                    package = load_skill_package(directory)
                except SkillPackageError:
                    raise SkillStoreFailure("skill_archive_invalid") from None
                if package.identifier != directory.name:
                    raise SkillStoreFailure("skill_archive_invalid")
                identifier = package.identifier
                if identifier in builtin_ids or (self.root / identifier).exists():
                    raise SkillStoreFailure("skill_already_exists")
                (directory / _ORIGIN_FILE).write_text(
                    json.dumps(request.source_type), encoding="utf-8"
                )
                destination = self.root / identifier
                try:
                    os.rename(directory, destination)
                except OSError:
                    if destination.exists():
                        raise SkillStoreFailure("skill_already_exists") from None
                    raise SkillStoreFailure("skill_registry_unavailable") from None
                return replace(package, source_directory=destination)
        except OSError:
            raise SkillStoreFailure("skill_registry_unavailable") from None
