from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from universal_agent_runtime.adapters.docker_agent_qwen import DockerAgentQwenRunner
from universal_agent_runtime.adapters.qwen_session import (
    DockerQwenCommandRunner,
    QwenInvocation,
    QwenSessionConfig,
)
from universal_agent_runtime.adapters.skill_packages import SkillPackageCatalog


class TaskCreationAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.config = QwenSessionConfig(
            storage_root=Path(self._temporary.name) / "sessions",
            base_url="http://10.21.171.2:11434/v1",
            model="qwen-3.8-multimodal:latest",
            api_key="test-placeholder",
            sfera_base_url="https://sfera.ai.dev.sfera-t1.ru",
            sfera_username="sfera-user",
            sfera_password="sfera-password",
            sfera_default_owner="sfera-admin",
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_skill_authorizes_create_task_only_for_explicit_current_intent(self) -> None:
        skill, granted = SkillPackageCatalog.builtins().resolve(
            ("task-decomposition",), ("get_task", "create_task")
        )[0]

        self.assertEqual(granted, ("get_task", "create_task"))
        self.assertEqual(
            skill.authorized_tools(granted, "Декомпозируй TTEST2-89"),
            ("get_task",),
        )
        self.assertEqual(
            skill.authorized_tools(granted, "Хорошо, давай дальше"),
            ("get_task",),
        )
        self.assertEqual(
            skill.authorized_tools(
                granted,
                "Декомпозируй TTEST2-89 на 4 задачи и создай их в Sfera",
            ),
            ("get_task", "create_task"),
        )
        self.assertEqual(
            skill.authorized_tools(
                granted, "Не создавай задачи в Sfera"
            ),
            ("get_task",),
        )

    def test_qwen_allows_only_granted_tools_and_bounds_create_workflow(self) -> None:
        runner = DockerQwenCommandRunner(self.config, client=object())
        invocation = QwenInvocation(
            Path(self._temporary.name) / "qwen-home",
            Path(self._temporary.name) / "workspace",
            uuid4(),
            "message",
            False,
            task_operations=("get_task", "create_task"),
        )

        command = runner.command(invocation)
        self.assertEqual(command[command.index("--max-tool-calls") + 1], "10")
        allowed = command.index("--allowed-tools")
        self.assertEqual(
            command[allowed + 1 : allowed + 3],
            ["task-rest__get_task", "task-rest__create_task"],
        )

    def test_owner_reaches_mcp_only_when_create_task_is_granted(self) -> None:
        runner = DockerAgentQwenRunner(
            self.config,
            workspace="/workspace",
            user="10001:10001",
            client=object(),
        )

        self.assertNotIn(
            "UAR_SFERA_DEFAULT_OWNER", runner._task_environment(("get_task",))
        )
        self.assertEqual(
            runner._task_environment(("get_task", "create_task"))["UAR_SFERA_DEFAULT_OWNER"],
            "sfera-admin",
        )


if __name__ == "__main__":
    unittest.main()
