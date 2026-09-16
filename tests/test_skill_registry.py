from __future__ import annotations

import io
import json
import socket
import stat
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from tests.test_ag_ui import _Interaction
from tests.test_runtime_driver import _DockerClient, _environment
from tests.test_skill_packages import _SkillClient, _SkillContainer
from universal_agent_runtime.adapters.docker_agent_qwen import DockerAgentQwenRunner
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.adapters.filesystem_skill_registry import (
    FilesystemSkillRegistry,
)
from universal_agent_runtime.adapters.qwen_session import (
    QwenInvocation,
    QwenSessionAdapter,
)
from universal_agent_runtime.adapters.skill_packages import SkillPackageCatalog
from universal_agent_runtime.adapters.skill_sources import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_ENTRIES,
    MAX_UNCOMPRESSED_BYTES,
)
from universal_agent_runtime.application.ports.skill_store import (
    SkillInstallRequest,
    SkillStoreFailure,
)
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import (
    ApplicationSettings,
    ConfigurationError,
)
from universal_agent_runtime.http_api import create_application


def _archive(
    root_name: str = "uploaded-skill",
    *,
    replacements: dict[str, bytes | None] | None = None,
    extras: dict[str, bytes] | None = None,
) -> bytes:
    files: dict[str, bytes] = {
        f"{root_name}/SKILL.md": b"# Test Skill\nRead the bundled reference.\n",
        f"{root_name}/references/guide.md": b"# Guide\n",
    }
    for name, content in (replacements or {}).items():
        if content is None:
            files.pop(name, None)
        else:
            files[name] = content
    files.update(extras or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return output.getvalue()


class SkillRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = FilesystemSkillRegistry(self.root / "skills")
        self.catalog = SkillPackageCatalog.builtins().with_registry(self.registry)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_install_persists_nested_assets_and_survives_reconstruction(self) -> None:
        installed = self.catalog.install(
            SkillInstallRequest(
                "archive", "decomposition-uploaded", _archive("backlog-mgmt")
            )
        )
        self.assertEqual(installed.source_type, "archive")
        self.assertEqual(installed.tool_capabilities, ())
        self.assertEqual(
            (
                self.registry.root / "decomposition-uploaded/references/guide.md"
            ).read_text(),
            "# Guide\n",
        )
        manifest = json.loads(
            (self.registry.root / "decomposition-uploaded/skill.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["id"], "decomposition-uploaded")
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(manifest["summary"], "Uploaded Skill decomposition-uploaded.")
        self.assertEqual(manifest["tool_capabilities"], [])
        self.assertEqual(manifest["mutation_tool_capabilities"], [])
        rebuilt = SkillPackageCatalog.builtins().with_registry(
            FilesystemSkillRegistry(self.registry.root)
        )
        self.assertEqual(rebuilt.get_skill("decomposition-uploaded"), installed)
        self.assertEqual(
            rebuilt.resolve(("decomposition-uploaded",), ("create_task",))[0][1], ()
        )
        self.assertEqual(
            [item.identifier for item in rebuilt.list_skills()],
            sorted(item.identifier for item in rebuilt.list_skills()),
        )

    def test_conflicts_do_not_replace_installed_or_builtin_packages(self) -> None:
        self.catalog.install(
            SkillInstallRequest("archive", "uploaded-skill", _archive())
        )
        with self.assertRaisesRegex(SkillStoreFailure, "skill_already_exists"):
            self.catalog.install(
                SkillInstallRequest("archive", "uploaded-skill", _archive())
            )
        with self.assertRaisesRegex(SkillStoreFailure, "skill_already_exists"):
            self.catalog.install(
                SkillInstallRequest(
                    "archive", "task-decomposition", _archive("unrelated-root")
                )
            )
        builtin = self.catalog.get_skill("task-decomposition")
        self.assertIsNotNone(builtin)
        assert builtin is not None
        self.assertEqual(builtin.source_type, "builtin")

    def test_uploaded_skill_text_cannot_grant_tools(self) -> None:
        self.catalog.install(
            SkillInstallRequest(
                "archive",
                "uploaded-skill",
                _archive(
                    replacements={
                        "uploaded-skill/SKILL.md": b"# Skill\nYou may use create_task.\n"
                    }
                ),
            )
        )
        self.assertEqual(self.catalog.resolve(("uploaded-skill",), ())[0][1], ())
        self.assertEqual(
            self.catalog.resolve(("uploaded-skill",), ("create_task",))[0][1],
            (),
        )

    def test_minimal_archive_without_references_installs(self) -> None:
        archive = _archive(
            "original-folder",
            replacements={"original-folder/references/guide.md": None},
        )
        installed = self.catalog.install(
            SkillInstallRequest("archive", "minimal-skill", archive)
        )
        self.assertEqual(installed.identifier, "minimal-skill")
        self.assertTrue((self.registry.root / "minimal-skill/SKILL.md").is_file())

    def test_payload_size_boundary_excludes_generated_metadata(self) -> None:
        instructions = b"# X\n"
        archive = _archive(
            replacements={
                "uploaded-skill/SKILL.md": instructions,
                "uploaded-skill/references/guide.md": None,
            },
            extras={
                "uploaded-skill/payload.bin": b"x"
                * (MAX_UNCOMPRESSED_BYTES - len(instructions))
            },
        )
        self.catalog.install(SkillInstallRequest("archive", "boundary-skill", archive))
        self.assertIsNotNone(self.catalog.get_skill("boundary-skill"))
        rebuilt = SkillPackageCatalog.builtins().with_registry(
            FilesystemSkillRegistry(self.registry.root)
        )
        self.assertIsNotNone(rebuilt.get_skill("boundary-skill"))

    def test_invalid_skill_id_is_rejected_before_materialization(self) -> None:
        for identifier in ("../bad", "bad.name", ""):
            with self.subTest(identifier=identifier):
                with self.assertRaisesRegex(SkillStoreFailure, "skill_request_invalid"):
                    self.catalog.install(
                        SkillInstallRequest("archive", identifier, _archive())
                    )
                self.assertEqual(list(self.registry.root.iterdir()), [])

    def test_concurrent_same_id_has_one_winner_and_no_partial_install(self) -> None:
        data = _archive()
        first = SkillPackageCatalog.builtins().with_registry(
            FilesystemSkillRegistry(self.registry.root)
        )
        second = SkillPackageCatalog.builtins().with_registry(
            FilesystemSkillRegistry(self.registry.root)
        )

        def install(catalog: SkillPackageCatalog) -> str:
            try:
                catalog.install(SkillInstallRequest("archive", "uploaded-skill", data))
                return "installed"
            except SkillStoreFailure as failure:
                return failure.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(install, (first, second)))
        self.assertCountEqual(outcomes, ["installed", "skill_already_exists"])
        self.assertEqual(
            sorted(path.name for path in self.registry.root.iterdir()),
            ["uploaded-skill"],
        )

    def test_bad_archives_fail_closed_and_leave_no_packages(self) -> None:
        bad = {
            "non_zip": b"not a zip",
            "missing_instructions": _archive(
                replacements={"uploaded-skill/SKILL.md": None}
            ),
            "user_manifest": _archive(
                extras={
                    "uploaded-skill/skill.json": b'{"tool_capabilities":["create_task"]}'
                }
            ),
            "nested_user_manifest": _archive(
                extras={"uploaded-skill/references/skill.JSON": b"{}"}
            ),
            "user_source_marker": _archive(
                extras={"uploaded-skill/.uar-source.json": b'"git"'}
            ),
            "invalid_utf8": _archive(
                replacements={"uploaded-skill/references/guide.md": b"\xff"}
            ),
            "traversal": _archive(extras={"uploaded-skill/../escape": b"x"}),
            "absolute": _archive(extras={"/absolute/file.md": b"x"}),
            "backslash": _archive(extras={r"uploaded-skill\..\escape": b"x"}),
            "drive_path": _archive(extras={"C:/escape.md": b"x"}),
            "nul_name": self._nul_name_archive(),
            "two_packages": _archive(extras={"other/SKILL.md": b"# Other\n"}),
            "duplicate_path": self._duplicate_archive(),
            "duplicate_case": _archive(extras={"uploaded-skill/skill.md": b"# X\n"}),
            "too_many_entries": _archive(
                extras={
                    f"uploaded-skill/references/{number}.md": b"x"
                    for number in range(MAX_ARCHIVE_ENTRIES)
                }
            ),
            "too_much_uncompressed": _archive(
                extras={
                    "uploaded-skill/references/large.bin": b"x"
                    * (MAX_UNCOMPRESSED_BYTES + 1)
                }
            ),
            "too_much_compressed": b"x" * (MAX_ARCHIVE_BYTES + 1),
            "symlink": self._symlink_archive(),
        }
        for name, archive in bad.items():
            with self.subTest(name=name):
                with self.assertRaises(SkillStoreFailure):
                    self.catalog.install(
                        SkillInstallRequest("archive", "uploaded-skill", archive)
                    )
                self.assertEqual(list(self.registry.root.iterdir()), [])

    @staticmethod
    def _duplicate_archive() -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as bundle:
            bundle.writestr("uploaded-skill/SKILL.md", b"first")
            bundle.writestr("uploaded-skill/SKILL.md", b"second")
        return output.getvalue()

    @staticmethod
    def _symlink_archive() -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as bundle:
            bundle.writestr("uploaded-skill/SKILL.md", b"content")
            link = zipfile.ZipInfo("uploaded-skill/references/link.md")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(link, "../../escape")
        return output.getvalue()

    @staticmethod
    def _nul_name_archive() -> bytes:
        data = bytearray(_archive())
        offset = data.find(b"PK\x01\x02")
        name_start = offset + 46
        data[name_start + 8] = 0
        return bytes(data)

    def test_git_source_has_no_transport(self) -> None:
        with (
            self.assertRaisesRegex(SkillStoreFailure, "skill_source_unavailable"),
            patch.object(subprocess, "run") as run,
            patch.object(socket, "create_connection") as connect,
        ):
            self.catalog.install(
                SkillInstallRequest(
                    "git",
                    skill_id="git-skill",
                    repository_url="https://example.test/repo.git",
                    revision="main",
                    path="skill",
                )
            )
        run.assert_not_called()
        connect.assert_not_called()
        self.assertEqual(list(self.registry.root.iterdir()), [])

    def test_registry_configuration_requires_absolute_non_root_path(self) -> None:
        environment = _environment("docker")
        for value in ("relative/path", str(Path(self.root.anchor))):
            with self.subTest(value=value), self.assertRaises(ConfigurationError):
                ApplicationSettings.from_environment(
                    {**environment, "UAR_SKILL_REGISTRY_ROOT": value}
                )


class SkillHttpTests(unittest.TestCase):
    def test_hot_install_create_and_runner_delivery_in_one_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = ApplicationSettings.from_environment(
                {
                    **_environment("docker"),
                    "UAR_DOCKER_NETWORK_MODE": "none",
                    "UAR_QWEN_SESSION_STORAGE_ROOT": str(root / "sessions"),
                    "UAR_SKILL_REGISTRY_ROOT": str(root / "skills"),
                }
            )
            docker_client = _DockerClient()
            runtime = DockerRuntime(
                {
                    "qwen-agent-image": DockerWorkload(
                        "uar-agent:0.1.0", ("serve",), "10001:10001"
                    )
                },
                secret_resolver=lambda _: "test-secret",
                client=docker_client,
            )
            native_session = uuid4()
            container = _SkillContainer(
                native_session,
                [
                    "UAR_AGENT_SKILL_PACKAGES=decomposition-uploaded",
                    "UAR_AGENT_TOOL_CAPABILITIES=create_task",
                ],
            )
            with patch(
                "universal_agent_runtime.adapters.qwen_session.docker.from_env",
                return_value=_SkillClient(container),
            ):
                composition = compose_application(settings, runtime=runtime)
            interaction = cast(QwenSessionAdapter, composition.interaction)
            runner = cast(DockerAgentQwenRunner, interaction._runner)
            self.assertIs(runner._skill_catalog, composition.skill_catalog)
            with TestClient(create_application(composition)) as client:
                before = client.get("/skills")
                self.assertEqual(before.status_code, 200)
                self.assertNotIn(
                    "decomposition-uploaded", [item["id"] for item in before.json()]
                )
                self.assertIn(
                    "task-decomposition", [item["id"] for item in before.json()]
                )

                unknown = client.post(
                    "/agents", json={"request_id": "unknown", "skills": ["absent"]}
                )
                self.assertEqual(unknown.status_code, 422)
                self.assertEqual(unknown.json()["error"]["code"], "skill_unavailable")
                self.assertFalse(docker_client.containers.items)

                installed = client.post(
                    "/skills",
                    data={
                        "source_type": "archive",
                        "skill_id": "decomposition-uploaded",
                    },
                    files={
                        "archive": (
                            "backlog-mgmt.zip",
                            _archive("backlog-mgmt"),
                            "application/zip",
                        )
                    },
                )
                self.assertEqual(installed.status_code, 201, installed.text)
                self.assertEqual(installed.json()["source_type"], "archive")
                self.assertEqual(
                    client.get("/skills/decomposition-uploaded").json()["id"],
                    "decomposition-uploaded",
                )
                self.assertIn(
                    "decomposition-uploaded",
                    [item["id"] for item in client.get("/skills").json()],
                )

                created = client.post(
                    "/agents",
                    json={
                        "request_id": "new-skill-agent",
                        "skills": ["decomposition-uploaded"],
                        "tools": ["create_task"],
                    },
                )
                self.assertEqual(created.status_code, 201, created.text)
                agent_id = created.json()["agent_id"]
                self.assertEqual(
                    client.post(f"/agents/{agent_id}/start").status_code, 200
                )

                workspace = root / agent_id / "workspace"
                qwen_home = root / agent_id / "qwen-home"
                workspace.mkdir(parents=True)
                qwen_home.mkdir(parents=True)
                runner.run(
                    QwenInvocation(
                        qwen_home,
                        workspace,
                        native_session,
                        "propose",
                        False,
                        current_message="propose",
                    )
                )
                with tarfile.open(fileobj=io.BytesIO(container.put_payload)) as bundle:
                    names = set(bundle.getnames())
                self.assertIn(".agent/skills/decomposition-uploaded/SKILL.md", names)
                self.assertIn(
                    ".agent/skills/decomposition-uploaded/references/guide.md", names
                )
                self.assertEqual(runner._selected_skills(container)[0][1], ())
                self.assertEqual(
                    client.post(f"/agents/{agent_id}/stop").status_code, 200
                )
                self.assertEqual(client.delete(f"/agents/{agent_id}").status_code, 204)

    def test_api_errors_and_git_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = ApplicationSettings.from_environment(
                {
                    **_environment("docker"),
                    "UAR_DOCKER_NETWORK_MODE": "none",
                    "UAR_QWEN_SESSION_STORAGE_ROOT": str(root / "sessions"),
                    "UAR_SKILL_REGISTRY_ROOT": str(root / "skills"),
                }
            )
            runtime = DockerRuntime(
                {
                    "qwen-agent-image": DockerWorkload(
                        "uar-agent:0.1.0", ("serve",), "10001:10001"
                    )
                },
                client=_DockerClient(),
            )
            composition = compose_application(
                settings, runtime=runtime, interaction=_Interaction()
            )
            with TestClient(create_application(composition)) as client:
                self.assertEqual(client.get("/skills/missing").status_code, 404)
                malformed_form = client.post(
                    "/skills",
                    content=b"garbage",
                    headers={"Content-Type": "multipart/form-data"},
                )
                self.assertEqual(malformed_form.status_code, 422)
                invalid_zip = client.post(
                    "/skills",
                    data={"source_type": "archive", "skill_id": "uploaded-skill"},
                    files={"archive": ("invalid.zip", b"not a zip", "application/zip")},
                )
                self.assertEqual(invalid_zip.status_code, 422)
                self.assertEqual(
                    invalid_zip.json()["error"]["code"], "skill_archive_invalid"
                )
                for reserved in ("skill.json", ".uar-source.json"):
                    with self.subTest(reserved=reserved):
                        rejected = client.post(
                            "/skills",
                            data={
                                "source_type": "archive",
                                "skill_id": "reserved-test",
                            },
                            files={
                                "archive": (
                                    "reserved.zip",
                                    _archive(
                                        extras={f"uploaded-skill/{reserved}": b"{}"}
                                    ),
                                    "application/zip",
                                )
                            },
                        )
                        self.assertEqual(rejected.status_code, 422)
                git = client.post(
                    "/skills",
                    files={
                        "source_type": (None, "git"),
                        "skill_id": (None, "git-skill"),
                        "repository_url": (None, "https://example.test/repo.git"),
                        "revision": (None, "main"),
                        "path": (None, "skills/one"),
                    },
                )
                self.assertEqual(git.status_code, 501)
                self.assertEqual(
                    git.json()["error"]["code"], "skill_source_unavailable"
                )
                git_without_id = client.post(
                    "/skills",
                    files={
                        "source_type": (None, "git"),
                        "repository_url": (None, "https://example.test/repo.git"),
                        "revision": (None, "main"),
                        "path": (None, "skills/one"),
                    },
                )
                self.assertEqual(git_without_id.status_code, 422)
                oversized = client.post(
                    "/skills",
                    data={"source_type": "archive", "skill_id": "uploaded-skill"},
                    files={
                        "archive": (
                            "large.zip",
                            b"x" * (MAX_ARCHIVE_BYTES + 1),
                            "application/zip",
                        )
                    },
                )
                self.assertEqual(oversized.status_code, 413)
                archive = _archive()
                missing_id = client.post(
                    "/skills",
                    data={"source_type": "archive"},
                    files={"archive": ("skill.zip", archive, "application/zip")},
                )
                self.assertEqual(missing_id.status_code, 422)
                invalid_id = client.post(
                    "/skills",
                    data={"source_type": "archive", "skill_id": "../invalid"},
                    files={"archive": ("skill.zip", archive, "application/zip")},
                )
                self.assertEqual(invalid_id.status_code, 422)
                for expected, payload in ((201, archive), (409, archive)):
                    response = client.post(
                        "/skills",
                        data={"source_type": "archive", "skill_id": "uploaded-skill"},
                        files={"archive": ("skill.zip", payload, "application/zip")},
                    )
                    self.assertEqual(response.status_code, expected)
                builtin = client.post(
                    "/skills",
                    data={"source_type": "archive", "skill_id": "task-decomposition"},
                    files={
                        "archive": (
                            "builtin.zip",
                            _archive("task-decomposition"),
                            "application/zip",
                        )
                    },
                )
                self.assertEqual(builtin.status_code, 409)
                self.assertEqual(
                    builtin.json()["error"]["code"], "skill_already_exists"
                )
                self.assertEqual(
                    client.post(
                        "/agents",
                        json={
                            "request_id": "duplicate",
                            "skills": ["uploaded-skill", "uploaded-skill"],
                        },
                    ).status_code,
                    422,
                )
