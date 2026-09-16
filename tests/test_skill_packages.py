from __future__ import annotations

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from universal_agent_runtime.adapters.docker_agent_qwen import DockerAgentQwenRunner
from universal_agent_runtime.adapters.qwen_session import (
    QwenInvocation,
    QwenSessionConfig,
)
from universal_agent_runtime.adapters.skill_packages import (
    SkillPackageCatalog,
    SkillPackageError,
    load_skill_package,
)


class SkillPackageTests(unittest.TestCase):
    def test_builtin_discovery_is_deterministic_and_complete(self) -> None:
        catalog = SkillPackageCatalog.builtins()

        self.assertEqual(
            tuple(catalog._packages),
            (
                "code-implementation",
                "code-review",
                "code-testing",
                "decomposition-2",
                "task-decomposition",
            ),
        )
        self.assertEqual(
            tuple(SkillPackageCatalog.builtins()._packages), tuple(catalog._packages)
        )

    def test_knowledge_only_packages_are_selectable_without_tools(self) -> None:
        catalog = SkillPackageCatalog.builtins()
        selected = (
            "decomposition-2",
            "code-implementation",
            "code-testing",
            "code-review",
        )

        resolved = catalog.resolve(selected, ())

        self.assertEqual(tuple(package.identifier for package, _ in resolved), selected)
        for package, granted in resolved:
            with self.subTest(package=package.identifier):
                self.assertEqual(package.tool_capabilities, ())
                self.assertEqual(package.mutation_tool_capabilities, ())
                self.assertEqual(granted, ())
                self.assertEqual(
                    package.authorized_tools(("get_task",), "create task"), ()
                )

    def test_decomposition_two_has_bundled_reference_cards(self) -> None:
        package, granted = SkillPackageCatalog.builtins().resolve(
            ("decomposition-2",), ("get_task", "create_task")
        )[0]

        references = sorted(
            package.source_directory.joinpath("references").glob("*.md")
        )

        self.assertEqual(granted, ())
        self.assertEqual(len(references), 18)
        self.assertTrue(
            all(
                reference.read_text(encoding="utf-8").strip()
                for reference in references
            )
        )
        self.assertIn(".agent/skills/decomposition-2/", package.instructions)

    def test_runner_delivers_nested_selected_skill_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "agent-one" / "workspace"
            qwen_home = root / "agent-one" / "qwen-home"
            workspace.mkdir(parents=True)
            qwen_home.mkdir(parents=True)
            native_session = uuid4()
            container = _SkillContainer(
                native_session,
                [
                    "UAR_AGENT_SKILL_PACKAGES=decomposition-2",
                    "UAR_AGENT_TOOL_CAPABILITIES=get_task,create_task",
                ],
            )
            runner = DockerAgentQwenRunner(
                QwenSessionConfig(
                    storage_root=root / "sessions",
                    base_url="http://ollama.example/v1",
                    model="test-model",
                    api_key="test-placeholder",
                ),
                workspace="/workspace",
                user="10001:10001",
                client=_SkillClient(container),
            )

            runner.run(
                QwenInvocation(
                    qwen_home,
                    workspace,
                    native_session,
                    "Propose a decomposition.",
                    False,
                    current_message="Propose a decomposition.",
                )
            )

            with tarfile.open(fileobj=io.BytesIO(container.put_payload)) as bundle:
                names = set(bundle.getnames())
            self.assertIn(".agent/skills/decomposition-2/SKILL.md", names)
            self.assertIn(".agent/skills/decomposition-2/skill.json", names)
            self.assertIn(
                ".agent/skills/decomposition-2/references/BLG.QLReference.md", names
            )

    def test_unknown_and_duplicate_selected_skills_are_rejected(self) -> None:
        catalog = SkillPackageCatalog.builtins()

        with self.assertRaisesRegex(SkillPackageError, "unavailable"):
            catalog.resolve(("unknown",), ())
        with self.assertRaisesRegex(SkillPackageError, "unique"):
            catalog.resolve(("code-testing", "code-testing"), ())

    def test_invalid_capability_manifest_remains_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "invalid"
            package.mkdir()
            (package / "SKILL.md").write_text("instructions", encoding="utf-8")
            (package / "skill.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "invalid",
                        "version": "1.0.0",
                        "summary": "Invalid capability example.",
                        "instruction_file": "SKILL.md",
                        "tool_capabilities": [],
                        "mutation_tool_capabilities": ["create_task"],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SkillPackageError, "mutation"):
                load_skill_package(package)

    def test_discovery_rejects_duplicate_ids_and_invalid_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._write_package(root / "first", "same")
            self._write_package(root / "second", "same")
            with (
                patch(
                    "universal_agent_runtime.adapters.skill_packages.resources.files",
                    return_value=root,
                ),
                self.assertRaisesRegex(SkillPackageError, "unique"),
            ):
                SkillPackageCatalog.builtins()

            (root / "second" / "skill.json").write_text("not json", encoding="utf-8")
            with (
                patch(
                    "universal_agent_runtime.adapters.skill_packages.resources.files",
                    return_value=root,
                ),
                self.assertRaisesRegex(SkillPackageError, "invalid"),
            ):
                SkillPackageCatalog.builtins()

    @staticmethod
    def _write_package(directory: Path, identifier: str) -> None:
        directory.mkdir()
        (directory / "SKILL.md").write_text("instructions", encoding="utf-8")
        (directory / "skill.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": identifier,
                    "version": "1.0.0",
                    "summary": "A valid test package.",
                    "instruction_file": "SKILL.md",
                    "tool_capabilities": [],
                    "mutation_tool_capabilities": [],
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()


class _SkillClient:
    def __init__(self, container: _SkillContainer) -> None:
        self.containers = self
        self._container = container

    def list(self, **_: object) -> list[_SkillContainer]:
        return [self._container]

    def close(self) -> None:
        return None


class _ExecResult:
    def __init__(self, output: bytes) -> None:
        self.exit_code = 0
        self.output = output


class _SkillContainer:
    status = "running"

    def __init__(self, session_id: object, environment: list[str]) -> None:
        self.attrs = {"Config": {"Env": environment}}
        self._session_id = session_id
        self.put_payload = b""
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w"):
            pass
        self._qwen_home_archive = archive.getvalue()

    def exec_run(self, command: list[str], **_: object) -> _ExecResult:
        if command[:2] == ["node", "-e"]:
            return _ExecResult(b"")
        return _ExecResult(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": str(self._session_id),
                    "result": "completed",
                }
            ).encode("utf-8")
        )

    def put_archive(self, _: str, payload: bytes) -> bool:
        self.put_payload = payload
        return True

    def get_archive(self, _: str) -> tuple[list[bytes], object]:
        return [self._qwen_home_archive], object()
