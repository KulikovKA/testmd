import json
import unittest
from types import SimpleNamespace

from universal_agent_runtime.adapters.docker_development_workspace import (
    DockerDevelopmentWorkspaceAdapter,
    workspace_result,
)
from universal_agent_runtime.adapters.repository_access import ConfiguredSecretPolicy
from universal_agent_runtime.application.ports.development_workspace import (
    ProjectFile,
    WorkspaceRequest,
    validate_project_path,
)
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceOperation as O,
)
from universal_agent_runtime.domain.development_task import DevelopmentFailure
from universal_agent_runtime.domain.identifiers import AgentId


class WorkspaceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_executor_uses_only_nonroot_owned_container(self):
        calls = []

        class Container:
            status = "running"

            def exec_run(self, argv, **options):
                calls.append((argv, options))
                return SimpleNamespace(
                    exit_code=0, output=(b'{"success":true}', b"private diagnostic")
                )

        client = SimpleNamespace(
            containers=SimpleNamespace(list=lambda **kwargs: [Container()])
        )
        adapter = DockerDevelopmentWorkspaceAdapter(
            client, ConfiguredSecretPolicy(("private-key",))
        )
        result = await adapter.execute(
            AgentId("agent-one"), WorkspaceRequest("task-one", O.PREPARE)
        )
        self.assertTrue(result.success)
        argv, options = calls[0]
        self.assertEqual(
            argv[:2], ["node", "/usr/local/lib/uar/workspace-operations.mjs"]
        )
        self.assertEqual(options["user"], "10001:10001")
        self.assertNotIn("private-key", repr(calls))
        self.assertNotIn("private diagnostic", result.output)
        with self.assertRaises(DevelopmentFailure):
            await adapter.execute(
                AgentId("agent-one"),
                WorkspaceRequest(
                    "task-one",
                    O.WRITE,
                    files=(ProjectFile("token.txt", "private-key"),),
                ),
            )
        self.assertEqual(len(calls), 1)

    async def test_public_results_redact_logs_and_reject_secret_files(self):
        policy = ConfiguredSecretPolicy(("private-key",))
        result = workspace_result(b'{"success":false,"output":"private-key"}', policy)
        self.assertEqual(result.output, "[REDACTED]")
        raw = json.dumps(
            {"success": True, "files": [{"path": "App.java", "content": "private-key"}]}
        ).encode()
        with self.assertRaises(DevelopmentFailure):
            workspace_result(raw, policy)

    async def test_staged_secret_is_rejected_before_commit_instead_of_only_redacted(
        self,
    ):
        container = SimpleNamespace(
            status="running",
            exec_run=lambda *args, **kwargs: SimpleNamespace(
                exit_code=0, output=(b'{"success":true,"output":"+private-key"}', None)
            ),
        )
        adapter = DockerDevelopmentWorkspaceAdapter(
            SimpleNamespace(
                containers=SimpleNamespace(list=lambda **kwargs: [container])
            ),
            ConfiguredSecretPolicy(("private-key",)),
        )
        with self.assertRaises(DevelopmentFailure) as raised:
            await adapter.execute(
                AgentId("agent-one"), WorkspaceRequest("task-one", O.DIFF)
            )
        self.assertEqual(raised.exception.code, "secret_rejected")

    async def test_paths_and_case_collisions_fail_before_container_execution(self):
        for path in (
            "../x",
            "a\\b",
            ".git/config",
            ".env",
            "a/../b",
            "/x",
            "C:/x",
            "con.txt",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_project_path(path)
        with self.assertRaises(ValueError):
            WorkspaceRequest(
                "task",
                O.WRITE,
                files=(ProjectFile("A.java", "a"), ProjectFile("a.java", "b")),
            )
