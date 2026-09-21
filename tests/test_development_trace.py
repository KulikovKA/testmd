"""Workflow facts drive both REST history and protocol-compatible live events."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from tests.test_development_workflow import Model, Workspace, collect, workflow_fixture
from universal_agent_runtime.adapters.repository_access import ConfiguredSecretPolicy
from universal_agent_runtime.ag_ui import AGUIEventResponse, ag_ui_events
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceOperation as W,
)
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceResult,
)
from universal_agent_runtime.application.ports.interaction_values import (
    AssistantTextDelta,
)
from universal_agent_runtime.domain.development_task import (
    DevelopmentFailure,
    DevelopmentRequest,
)
from universal_agent_runtime.domain.development_trace import MAX_TRACE_EVENTS

ALLOWED = {
    "RUN_STARTED",
    "STEP_STARTED",
    "STEP_FINISHED",
    "CUSTOM",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "RUN_FINISHED",
    "RUN_ERROR",
}


class DevelopmentTraceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def assert_protocol(self, events):
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        active = None
        seen = set()
        for event in events:
            self.assertIn(event["type"], ALLOWED)
            if event["type"] == "STEP_STARTED":
                self.assertIsNone(active)
                active = event["stepName"]
                self.assertNotIn(active, seen)
                seen.add(active)
                self.assertEqual(set(event), {"type", "stepName"})
            elif event["type"] == "STEP_FINISHED":
                self.assertEqual(active, event["stepName"])
                active = None
            elif event["type"] == "CUSTOM":
                self.assertEqual(set(event), {"type", "name", "value"})
                self.assertIsInstance(event["name"], str)
                self.assertIsInstance(event["value"], dict)
            elif event["type"] == "TEXT_MESSAGE_START":
                self.assertIsNone(active)
        self.assertIsNone(active)

    async def test_completed_trace_matches_stream_and_contains_only_real_actions(self):
        workflow, agent_id, _, workspace = workflow_fixture(self.root)
        task = workflow.create(DevelopmentRequest(agent_id, "Java library"))
        with patch(
            "universal_agent_runtime.application.development_workflow.time.perf_counter",
            return_value=1,
        ):
            events = await collect(workflow.begin(task.task_id))
        self.assert_protocol(events)
        trace = [e.to_dict() for e in workflow.service.get(task.task_id).trace]
        self.assertEqual(trace, [e["value"] for e in events if e["type"] == "CUSTOM"])
        self.assertEqual([e["sequence"] for e in trace], list(range(1, len(trace) + 1)))
        by_name = {e["type"]: e for e in trace}
        self.assertTrue(by_name["requirements_result"]["data"]["sufficient"])
        self.assertEqual(by_name["plan_ready"]["data"]["steps"], 2)
        self.assertEqual(
            by_name["files_changed"]["data"]["created"],
            ["pom.xml", "src/main/java/App.java"],
        )
        self.assertTrue(by_name["review_result"]["data"]["approved"])
        self.assertEqual(by_name["git_commit"]["data"]["commit_id"], "a" * 40)
        self.assertIn(W.COMMIT, workspace.operations)
        commands = [e for e in trace if e["type"] == "command_finished"]
        self.assertEqual(
            [e["data"]["command"] for e in commands], ["mvn test", "mvn package"]
        )
        self.assertTrue(all(e["data"]["success"] for e in commands))
        self.assertTrue(
            all(e["data"]["exit_code"] is None for e in commands)
        )  # Unknown is never invented.
        text = "".join(e["delta"] for e in events if "delta" in e)
        self.assertNotIn('"files"', text)
        self.assertNotIn("public class", json.dumps(events))
        self.assertEqual(
            [e["type"] for e in events[-4:]],
            [
                "TEXT_MESSAGE_START",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_END",
                "RUN_FINISHED",
            ],
        )
        # Sequence and semantic data are deterministic for the same execution.
        other, aid, _, _ = workflow_fixture(self.root)
        another = other.create(DevelopmentRequest(aid, "Java library"))
        with patch(
            "universal_agent_runtime.application.development_workflow.time.perf_counter",
            return_value=1,
        ):
            await collect(other.begin(another.task_id))
        self.assertEqual(
            trace, [e.to_dict() for e in other.service.get(another.task_id).trace]
        )

    async def test_fix_loop_pairs_attempts_and_does_not_hide_failure(self):
        workflow, aid, _, _ = workflow_fixture(
            self.root, Model(review_failure=True), Workspace(fail_builds=1)
        )
        task = workflow.create(DevelopmentRequest(aid, "Java library"))
        events = await collect(workflow.begin(task.task_id))
        self.assert_protocol(events)
        self.assertEqual(
            [
                e["stepName"]
                for e in events
                if e["type"] == "STEP_STARTED" and e["stepName"].startswith("testing:")
            ],
            ["testing:1", "testing:2", "testing:3"],
        )
        trace = workflow.service.get(task.task_id).trace
        tests = [
            e.to_dict()["data"]["success"]
            for e in trace
            if e.type == "command_finished"
        ]
        self.assertEqual(tests, [False, True, True, True, True])
        self.assertEqual(
            [e.status for e in trace if e.type == "review_result"],
            ["failed", "completed"],
        )

    async def test_failed_write_and_commit_never_claim_success(self):
        class Failed(Workspace):
            async def execute(self, agent_id, request):
                if request.operation is self.fail_operation:
                    raise DevelopmentFailure("operation_failed")
                return await super().execute(agent_id, request)

        for operation, forbidden in (
            (W.WRITE, "files_changed"),
            (W.COMMIT, "git_commit"),
        ):
            workspace = Failed()
            workspace.fail_operation = operation
            workflow, aid, _, _ = workflow_fixture(self.root, workspace=workspace)
            task = workflow.create(DevelopmentRequest(aid, "Java library"))
            events = await collect(workflow.begin(task.task_id))
            self.assert_protocol(events)
            self.assertEqual(events[-1]["type"], "RUN_ERROR")
            trace = workflow.service.get(task.task_id).trace
            self.assertNotIn(forbidden, [e.type for e in trace])
            self.assertEqual(trace[-1].status, "failed")

    async def test_active_and_idle_cancellation_and_secret_diagnostics(self):
        class Gated(Workspace):
            async def execute(self, agent_id, request):
                result = await super().execute(agent_id, request)
                if request.operation is W.TEST:
                    return WorkspaceResult(
                        False,
                        "Authorization: Bearer UNEXPOSED_BUILD_SECRET",
                        exit_code=2,
                    )
                return result

        workspace = Gated()
        workspace.release.clear()
        workflow, aid, _, _ = workflow_fixture(self.root, workspace=workspace)
        workflow.secrets = ConfiguredSecretPolicy(("UNEXPOSED_BUILD_SECRET",))
        task = workflow.create(DevelopmentRequest(aid, "Java library"))
        turn = workflow.begin(task.task_id)
        consumer = asyncio.create_task(collect(turn))
        await asyncio.wait_for(workspace.entered.wait(), 5)
        workflow.service.cancel(task.task_id)
        workspace.release.set()
        events = await consumer
        self.assert_protocol(events)
        self.assertEqual(events[-1]["type"], "RUN_ERROR")
        trace = [e.to_dict() for e in workflow.service.get(task.task_id).trace]
        self.assertEqual(trace[-1]["status"], "cancelled")
        self.assertNotIn(
            "UNEXPOSED_BUILD_SECRET", json.dumps(trace) + json.dumps(events)
        )
        self.assertNotIn("Authorization", json.dumps(trace) + json.dumps(events))
        self.assertEqual(
            [e for e in trace if e["type"] == "command_finished"][-1]["data"][
                "exit_code"
            ],
            2,
        )
        idle = workflow.create(DevelopmentRequest(aid, "Java library"))
        workflow.service.cancel(idle.task_id)
        self.assertEqual(
            workflow.service.get(idle.task_id).trace[-1].status, "cancelled"
        )

    async def test_header_disconnect_preserves_trace_without_deadlock(self):
        workflow, aid, _, _ = workflow_fixture(self.root)
        task = workflow.create(DevelopmentRequest(aid, "Java library"))
        turn = workflow.begin(task.task_id)
        response = AGUIEventResponse(
            ag_ui_events(turn, thread_id="t", run_id="r", heartbeat_seconds=1),
            turn=turn,
        )

        async def reject_headers(_message):
            raise OSError("disconnected")

        with self.assertRaises(OSError):
            await response.stream_response(reject_headers)
        await asyncio.wait_for(asyncio.shield(turn.task), 5)
        self.assertEqual(
            workflow.service.get(task.task_id).trace[-1].status, "completed"
        )
        self.assertEqual(turn.progress.queue.maxsize, 16)

    async def test_trace_quota_fails_closed_with_terminal_record(self):
        workflow, aid, _, _ = workflow_fixture(self.root)
        task = workflow.create(DevelopmentRequest(aid, "Java library"))
        for _ in range(MAX_TRACE_EVENTS - 8):
            workflow.service.record(
                task.task_id,
                type="task_status",
                phase="task",
                status="waiting",
                step_name=None,
                attempt=0,
                summary="Ожидание",
            )
        events = await collect(workflow.begin(task.task_id))
        self.assert_protocol(events)
        self.assertEqual(events[-1]["type"], "RUN_ERROR")
        final = workflow.service.get(task.task_id)
        self.assertEqual(final.failure_code, "output_limit")
        self.assertEqual(final.trace[-1].status, "failed")
        self.assertLessEqual(len(final.trace), MAX_TRACE_EVENTS)

    async def test_model_secret_never_becomes_public_progress_or_a_file(self):
        class SecretModel(Model):
            async def turn_stream(self, request, on_delta):
                async def discard(_delta):
                    pass

                result = await super().turn_stream(request, discard)
                response = result.response.replace(
                    "public class App", "MODEL_PRIVATE_SECRET public class App"
                )
                await on_delta(AssistantTextDelta(response))
                return replace(result, response=response)

        workflow, aid, _, workspace = workflow_fixture(self.root, model=SecretModel())
        workflow.secrets = ConfiguredSecretPolicy(("MODEL_PRIVATE_SECRET",))
        task = workflow.create(DevelopmentRequest(aid, "Java library"))
        events = await collect(workflow.begin(task.task_id))
        trace = [e.to_dict() for e in workflow.service.get(task.task_id).trace]
        self.assertNotIn("MODEL_PRIVATE_SECRET", json.dumps(events) + json.dumps(trace))
        self.assertNotIn(W.WRITE, workspace.operations)
        self.assertEqual(
            workflow.service.get(task.task_id).failure_code, "secret_rejected"
        )

    async def test_successful_inflight_write_is_recorded_before_cancel_acknowledged(
        self,
    ):
        class WriteGate(Workspace):
            async def execute(self, aid, request):
                if request.operation is W.WRITE:
                    self.entered.set()
                    await self.release.wait()
                return await super().execute(aid, request)

        workspace = WriteGate()
        workspace.release.clear()
        workflow, aid, _, _ = workflow_fixture(self.root, workspace=workspace)
        task = workflow.create(DevelopmentRequest(aid, "Java library"))
        turn = workflow.begin(task.task_id)
        consumer = asyncio.create_task(collect(turn))
        await asyncio.wait_for(workspace.entered.wait(), 5)
        workflow.service.cancel(task.task_id)
        workspace.release.set()
        events = await consumer
        self.assert_protocol(events)
        self.assertIn(
            "files_changed", [e.type for e in workflow.service.get(task.task_id).trace]
        )
        self.assertNotIn(W.TEST, workspace.operations)
