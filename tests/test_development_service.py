import unittest
from dataclasses import replace

from tests.test_ag_ui_streaming import _chat, _Interaction
from universal_agent_runtime.adapters.in_memory_development_tasks import (
    InMemoryDevelopmentTaskRepository,
)
from universal_agent_runtime.application.agent_lifecycle import AgentLifecycleFailure
from universal_agent_runtime.application.development_tasks import DevelopmentTaskService
from universal_agent_runtime.domain.development_task import (
    DevelopmentFailure,
    DevelopmentRequest,
)
from universal_agent_runtime.domain.development_task import DevelopmentState as S


class DevelopmentServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        interaction = _Interaction(("answer",))
        interaction.release.set()
        self.chat, self.agents, self.agent_id = _chat(interaction)
        self.tasks = InMemoryDevelopmentTaskRepository()
        self.service = DevelopmentTaskService(self.tasks, self.agents)
        self.task = self.service.create(
            DevelopmentRequest(self.agent_id, "Java project")
        )

    async def test_ownership_blocks_external_chat_but_allows_owned_turn(self):
        self.service.claim(self.task.task_id)
        with self.assertRaises(AgentLifecycleFailure):
            self.chat.begin(self.agent_id, "interfering turn")
        with self.assertRaises(DevelopmentFailure):
            self.service.claim(self.task.task_id)
        turn = self.chat.begin(
            self.agent_id,
            "analysis",
            stream=True,
            development_task_id=self.task.task_id,
        )
        turn.deltas.detach()
        await turn.task
        self.assertEqual(
            self.agents.get(self.agent_id).development_task_id, self.task.task_id
        )
        self.service.release(self.task.task_id)
        self.assertIsNone(self.agents.get(self.agent_id).development_task_id)
        self.assertEqual(len(self.agents.get(self.agent_id).messages), 2)

    async def test_clarification_and_cancel_are_explicit(self):
        self.service.transition(self.task.task_id, S.ANALYZING_REQUIREMENTS)
        self.service.transition(
            self.task.task_id, S.WAITING_FOR_CLARIFICATION, questions=("Which API?",)
        )
        with self.assertRaises(DevelopmentFailure):
            self.service.claim(self.task.task_id)
        self.service.clarify(self.task.task_id, "A REST API")
        self.service.claim(self.task.task_id)
        self.assertTrue(self.service.cancel(self.task.task_id).cancel_requested)
        self.assertEqual(
            self.service.get(self.task.task_id).state, S.WAITING_FOR_CLARIFICATION
        )
        self.service.release(self.task.task_id)
        self.assertEqual(self.service.cancel(self.task.task_id).state, S.CANCELLED)

    async def test_stale_save_does_not_overwrite_newer_task(self):
        self.service.transition(self.task.task_id, S.ANALYZING_REQUIREMENTS)
        with self.assertRaises(DevelopmentFailure):
            self.tasks.save(replace(self.task, version=1), expected_version=0)
        self.assertEqual(
            self.service.get(self.task.task_id).state, S.ANALYZING_REQUIREMENTS
        )
