"""Bounded, untrusted Skill source materialization."""

import io
import re
import stat
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Protocol

from universal_agent_runtime.application.ports.skill_store import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_ENTRIES,
    MAX_UNCOMPRESSED_BYTES,
    SkillInstallRequest,
    SkillStoreFailure,
)

_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class SkillSource(Protocol):
    def materialize(self, request: SkillInstallRequest, staging: Path) -> Path: ...


class GitSkillSource:
    """Reserved source contract; deployment has no Git transport or credentials."""

    def materialize(self, request: SkillInstallRequest, staging: Path) -> Path:
        raise SkillStoreFailure("skill_source_unavailable")


class ArchiveSkillSource:
    """Extract exactly one safe UAR package without using ZipFile.extractall."""

    def materialize(self, request: SkillInstallRequest, staging: Path) -> Path:
        data = request.archive
        if data is None or not data:
            raise SkillStoreFailure("skill_archive_invalid")
        if len(data) > MAX_ARCHIVE_BYTES:
            raise SkillStoreFailure("skill_archive_too_large")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                self._validate_raw_names(data, archive)
                return self._extract(archive, staging)
        except (
            OSError,
            ValueError,
            RuntimeError,
            zipfile.BadZipFile,
            EOFError,
            zlib.error,
        ):
            raise SkillStoreFailure("skill_archive_invalid") from None

    @staticmethod
    def _validate_raw_names(data: bytes, archive: zipfile.ZipFile) -> None:
        """Catch embedded NULs before ZipInfo truncates a central-directory name."""

        offset = archive.start_dir
        for entry in archive.infolist():
            if data[offset : offset + 4] != b"PK\x01\x02" or offset + 46 > len(data):
                raise SkillStoreFailure("skill_archive_invalid")
            name_size, extra_size, comment_size = struct.unpack_from(
                "<HHH", data, offset + 28
            )
            start = offset + 46
            end = start + name_size
            raw = data[start:end]
            try:
                decoded = raw.decode("ascii")
            except UnicodeDecodeError:
                raise SkillStoreFailure("skill_archive_invalid") from None
            if "\x00" in decoded or decoded != entry.filename:
                raise SkillStoreFailure("skill_archive_invalid")
            offset = end + extra_size + comment_size

    @staticmethod
    def _extract(archive: zipfile.ZipFile, staging: Path) -> Path:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
            raise SkillStoreFailure(
                "skill_archive_too_large" if entries else "skill_archive_invalid"
            )
        seen: dict[str, bool] = {}
        roots: set[str] = set()
        total = 0
        for entry in entries:
            name = entry.filename
            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or "\x00" in name
                or ":" in name
            ):
                raise SkillStoreFailure("skill_archive_invalid")
            parts = name.rstrip("/").split("/")
            if any(
                part in {"", ".", ".."} or not _COMPONENT.fullmatch(part)
                for part in parts
            ):
                raise SkillStoreFailure("skill_archive_invalid")
            is_dir = entry.is_dir()
            if (len(parts) < 2 and not is_dir) or parts[-1] == ".uar-source.json":
                raise SkillStoreFailure("skill_archive_invalid")
            roots.add(parts[0])
            key = "/".join(parts).casefold()
            if key in seen:
                raise SkillStoreFailure("skill_archive_invalid")
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if mode not in {0, stat.S_IFDIR if is_dir else stat.S_IFREG} or (
                entry.external_attr & 0x400
            ):
                raise SkillStoreFailure("skill_archive_invalid")
            seen[key] = is_dir
            total += entry.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise SkillStoreFailure("skill_archive_too_large")
        if len(roots) != 1:
            raise SkillStoreFailure("skill_archive_invalid")
        for key in seen:
            parts = key.split("/")
            if any(
                seen.get("/".join(parts[:index])) is False
                for index in range(1, len(parts))
            ):
                raise SkillStoreFailure("skill_archive_invalid")
        root_name = next(iter(roots))
        package = staging / root_name
        remaining = MAX_UNCOMPRESSED_BYTES
        for entry in entries:
            parts = entry.filename.rstrip("/").split("/")
            destination = staging.joinpath(*parts)
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open("xb") as output:
                while True:
                    chunk = source.read(min(1024 * 1024, remaining + 1))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise SkillStoreFailure("skill_archive_too_large")
                    output.write(chunk)
            if destination.suffix.lower() in {".md", ".json"}:
                try:
                    destination.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    raise SkillStoreFailure("skill_archive_invalid") from None
        return package
