"""Safe workspace inventory through the owned Docker runtime archive."""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_ag_ui import _Interaction
from tests.test_runtime_driver import (
    _Container,
    _Containers,
    _DockerClient,
    _environment,
)
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application

SECRET = b"private-password-cookie-api_key-Authorization-value"


def _archive(entries: list[tuple[str, str, bytes]]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as bundle:
        root = tarfile.TarInfo("workspace")
        root.type = tarfile.DIRTYPE
        bundle.addfile(root)
        for path, kind, contents in entries:
            member = tarfile.TarInfo("workspace/" + path)
            if kind == "directory":
                member.type = tarfile.DIRTYPE
                bundle.addfile(member)
            elif kind == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = "/etc"
                bundle.addfile(member)
            else:
                member.size = len(contents)
                bundle.addfile(member, io.BytesIO(contents))
    return output.getvalue()


class _ArchiveContainer(_Container):
    def get_archive(self, path: str):
        assert path == "/workspace"
        owner = self._collection.owner
        if owner.unavailable:
            raise ValueError("backend unavailable")
        return iter((owner.archive[:512], owner.archive[512:])), {}


class _ArchiveContainers(_Containers):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def create(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        container = _ArchiveContainer(self, kwargs["name"])
        self.items[kwargs["name"]] = container
        return container


class _ArchiveClient(_DockerClient):
    def __init__(self):
        super().__init__()
        self.archive = _archive([])
        self.unavailable = False
        self.containers = _ArchiveContainers(self)


class AgentFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        settings = ApplicationSettings.from_environment(
            {
                **_environment("docker"),
                "UAR_DOCKER_NETWORK_MODE": "none",
                "UAR_QWEN_SESSION_STORAGE_ROOT": str(root / "sessions"),
                "UAR_SKILL_REGISTRY_ROOT": str(root / "skills"),
            }
        )
        self.docker = _ArchiveClient()
        runtime = DockerRuntime(
            {
                "qwen-agent-image": DockerWorkload(
                    "uar-agent:0.1.0", ("serve",), "10001:10001"
                )
            },
            secret_resolver=lambda _: "test-secret",
            client=self.docker,
        )
        app = create_application(
            compose_application(settings, runtime=runtime, interaction=_Interaction())
        )
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _create(self, skills: list[str] | None = None) -> str:
        response = self.client.post(
            "/agents", json={"request_id": "inventory-test", "skills": skills or []}
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["agent_id"]

    def _report(self, agent_id: str, suffix: str = "") -> dict:
        response = self.client.get(f"/agents/{agent_id}/files{suffix}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_skills_transcript_mcp_and_ca_are_metadata_only(self) -> None:
        agent_id = self._create(["code-testing", "code-review"])
        self.assertEqual(self.client.post(f"/agents/{agent_id}/start").status_code, 200)
        self.docker.archive = _archive(
            [
                (".agent", "directory", b""),
                (".agent/skills", "directory", b""),
                (".agent/skills/code-testing", "directory", b""),
                (".agent/skills/code-testing/SKILL.md", "file", SECRET),
                (".agent/skills/code-testing/skill.json", "file", SECRET),
                (".agent/skills/code-review", "directory", b""),
                (".agent/skills/code-review/SKILL.md", "file", SECRET),
                (".agent/skills/code-review/skill.json", "file", SECRET),
                (".qwen-home", "directory", b""),
                (".qwen-home/projects/-workspace/chats/turn.jsonl", "file", SECRET),
                (".uar-tools", "directory", b""),
                (".uar-tools/task_rest_mcp_server.mjs", "file", SECRET),
                (".uar-tools/sfera-ca.pem", "file", SECRET),
                (".env", "file", SECRET),
                ("credentials.json", "file", SECRET),
                ("cookie-value.txt", "file", SECRET),
                ("api_key.txt", "file", SECRET),
                ("Authorization-value.txt", "file", SECRET),
            ]
        )
        report = self._report(agent_id)
        paths = {entry["path"]: entry for entry in report["files"]}
        self.assertEqual(report["root"], "/workspace")
        self.assertEqual(report["state"], "READY")
        self.assertTrue(report["available"])
        self.assertIn(".agent/skills/code-testing/SKILL.md", paths)
        self.assertIn(".agent/skills/code-review/skill.json", paths)
        self.assertIn(".qwen-home/projects/-workspace/chats/turn.jsonl", paths)
        self.assertEqual(
            paths[".qwen-home/projects/-workspace/chats/turn.jsonl"]["category"],
            "qwen_transcript",
        )
        self.assertIn(".uar-tools/task_rest_mcp_server.mjs", paths)
        self.assertEqual(paths[".uar-tools/sfera-ca.pem"]["size_bytes"], len(SECRET))
        self.assertEqual(report["summary"]["skills"], 2)
        self.assertEqual(report["summary"]["transcripts"], 1)
        self.assertTrue(report["summary"]["mcp_server_present"])
        self.assertNotIn(".env", paths)
        self.assertNotIn("credentials.json", paths)
        self.assertNotIn("cookie-value.txt", paths)
        self.assertNotIn("api_key.txt", paths)
        self.assertNotIn("Authorization-value.txt", paths)
        self.assertNotIn(SECRET.decode(), json.dumps(report))

    def test_fixed_root_traversal_and_symlinks(self) -> None:
        agent_id = self._create()
        self.docker.archive = _archive(
            [
                ("normal.txt", "file", b"safe"),
                ("escape/outside.txt", "file", SECRET),
                ("escape", "symlink", b""),
            ]
        )
        report = self._report(agent_id, "?path=../../etc&root=/root")
        self.assertEqual([entry["path"] for entry in report["files"]], ["normal.txt"])
        self.assertEqual(report["root"], "/workspace")
        self.docker.archive = _archive([("../etc/passwd", "file", SECRET)])
        rejected = self._report(agent_id)
        self.assertFalse(rejected["available"])
        self.assertEqual(rejected["reason"], "workspace_not_available")
        self.assertNotIn(SECRET.decode(), json.dumps(rejected))

    def test_empty_stopped_and_unavailable_workspace(self) -> None:
        agent_id = self._create()
        empty = self._report(agent_id)
        self.assertTrue(empty["available"])
        self.assertEqual(empty["files"], [])
        self.assertEqual(self.client.post(f"/agents/{agent_id}/start").status_code, 200)
        self.assertEqual(self.client.post(f"/agents/{agent_id}/stop").status_code, 200)
        self.assertTrue(self._report(agent_id)["available"])
        self.docker.unavailable = True
        unavailable = self._report(agent_id)
        self.assertFalse(unavailable["available"])
        self.assertEqual(unavailable["reason"], "workspace_not_available")

    def test_entry_depth_and_response_limits(self) -> None:
        agent_id = self._create()
        entries = [("/".join(["deep"] * 9) + "/hidden.txt", "file", SECRET)]
        entries.extend(
            (f"file-{number:04d}.txt", "file", b"") for number in range(1100)
        )
        self.docker.archive = _archive(entries)
        report = self._report(agent_id)
        self.assertTrue(report["available"])
        self.assertTrue(report["truncated"])
        self.assertLessEqual(len(report["files"]), 1000)
        self.assertLess(len(json.dumps(report).encode("utf-8")), 256 * 1024)
        self.assertFalse(
            any("hidden.txt" in entry["path"] for entry in report["files"])
        )
        long_names = [
            (f"{number:04d}-" + "x" * 115 + ".txt", "file", b"")
            for number in range(900)
        ]
        self.docker.archive = _archive(long_names)
        bounded = self._report(agent_id)
        self.assertTrue(bounded["truncated"])
        self.assertLess(len(bounded["files"]), 900)
        self.assertLess(len(json.dumps(bounded).encode("utf-8")), 256 * 1024)

    def test_unknown_agent_has_safe_inspect_404(self) -> None:
        files = self.client.get("/agents/absent/files")
        inspect = self.client.get("/agents/absent")
        self.assertEqual(files.status_code, 404)
        self.assertEqual(files.json(), inspect.json())
