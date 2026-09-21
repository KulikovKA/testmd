import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_ag_ui import _events, _Interaction
from tests.test_ag_ui_streaming import _chat
from universal_agent_runtime.adapters.docker_development_workspace import (
    workspace_payload,
    workspace_result,
)
from universal_agent_runtime.adapters.in_memory_development_tasks import (
    InMemoryDevelopmentTaskRepository,
)
from universal_agent_runtime.adapters.repository_access import (
    ConfiguredSecretPolicy,
)
from universal_agent_runtime.ag_ui import ag_ui_events
from universal_agent_runtime.application.agent_lifecycle import AgentLifecycleFailure
from universal_agent_runtime.application.development_tasks import DevelopmentTaskService
from universal_agent_runtime.application.development_workflow import (
    JAVA_SKILLS,
    DevelopmentWorkflow,
)
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceOperation as W,
)
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceResult,
)
from universal_agent_runtime.application.ports.interaction_values import (
    AssistantTextDelta,
    TurnResult,
)
from universal_agent_runtime.domain.development_task import (
    DevelopmentFailure,
    DevelopmentRequest,
)
from universal_agent_runtime.domain.development_task import DevelopmentState as S


class Model(_Interaction):
    def __init__(self, *, clarify=False, review_failure=False, invalid_file=False):
        super().__init__()
        self.clarify, self.review_failure, self.invalid_file = (
            clarify,
            review_failure,
            invalid_file,
        )
        self.phases = []

    async def turn_stream(self, request, on_delta):
        phase = request.message.splitlines()[0].split(": ")[1]
        self.phases.append(phase)
        if phase == "ANALYZING_REQUIREMENTS":
            answer = {
                "questions": ["CLI or library?"] if self.clarify else [],
                "summary": "Java project",
            }
            self.clarify = False
        elif phase == "PLANNING":
            answer = {"steps": ["Create Java project", "Test and review"]}
        elif phase in {"IMPLEMENTING", "FIXING"}:
            answer = {
                "files": [
                    {
                        "path": "../escape"
                        if self.invalid_file
                        else "src/main/java/App.java",
                        "content": "public class App { public static int sum(int a, int b) { return a + b; } }\n",
                    },
                    {"path": "pom.xml", "content": "<project/>\n"},
                ]
            }
        else:
            answer = {
                "approved": not self.review_failure,
                "findings": ["Improve tests"] if self.review_failure else [],
            }
            self.review_failure = False
        text = json.dumps(answer)
        for index in range(0, len(text), 9):
            await on_delta(AssistantTextDelta(text[index : index + 9]))
        turns = self._turns.setdefault(request.session, [])
        turns.append(request.message)
        return TurnResult(request.session, len(turns), text)


class Workspace:
    execution_backend = "deterministic-test-double"

    def __init__(self, fail_builds=0):
        self.files = ()
        self.operations = []
        self.fail_builds = fail_builds
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    async def execute(self, agent_id, request):
        self.operations.append(request.operation)
        if request.operation is W.WRITE:
            self.files = request.files
        if request.operation in {W.TEST, W.PACKAGE}:
            self.entered.set()
            await self.release.wait()
            if self.fail_builds:
                self.fail_builds -= 1
                return WorkspaceResult(False, "compilation failed")
            return WorkspaceResult(True, check=f"fake-{request.operation.value}")
        return WorkspaceResult(
            True,
            files=self.files if request.operation is W.INVENTORY else (),
            commit_id="a" * 40 if request.operation is W.COMMIT else None,
        )


def workflow_fixture(root, model=None, workspace=None):
    model, workspace = model or Model(), workspace or Workspace()
    chat, agents, agent_id = _chat(model)
    record = agents.get(agent_id)
    agents.save(
        replace(record, configuration=replace(record.configuration, skills=JAVA_SKILLS))
    )
    workflow = DevelopmentWorkflow(
        DevelopmentTaskService(InMemoryDevelopmentTaskRepository(), agents),
        chat,
        workspace,
        ConfiguredSecretPolicy(),
    )
    return workflow, agent_id, model, workspace


async def collect(turn):
    frames = [
        frame
        async for frame in ag_ui_events(
            turn, thread_id="thread", run_id="run", heartbeat_seconds=1
        )
    ]
    return _events(b"".join(frames).decode())


class DevelopmentWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    async def test_build_and_review_fix_loops_preserve_native_turn_history(self):
        workflow, agent_id, model, workspace = workflow_fixture(
            self.root, Model(review_failure=True), Workspace(fail_builds=1)
        )
        task = workflow.create(DevelopmentRequest(agent_id, "Java sum library"))
        turn = workflow.begin(task.task_id)
        with self.assertRaises(AgentLifecycleFailure):
            workflow.chat.begin(agent_id, "overlap")
        with self.assertRaises(DevelopmentFailure):
            workflow.begin(task.task_id)
        events = await collect(turn)
        result = workflow.service.get(task.task_id)
        self.assertEqual(result.state, S.COMPLETED)
        self.assertEqual(result.fix_attempts, 2)
        self.assertEqual(result.result.checks, ("fake-test", "fake-package"))
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertGreater(sum(e["type"] == "CUSTOM" for e in events), 20)
        self.assertEqual(sum("delta" in e for e in events), 1)
        self.assertEqual(
            "".join(e["delta"] for e in events if "delta" in e),
            turn.task.result()[1].content,
        )
        record = workflow.service.agents.get(agent_id)
        self.assertEqual(len(record.messages), len(model.phases) * 2)
        self.assertIsNone(record.development_task_id)
        self.assertEqual(workspace.operations.count(W.COMMIT), 1)
        self.assertNotIn(W.PUSH, workspace.operations)

    async def test_clarification_then_resume_uses_same_native_session(self):
        workflow, agent_id, model, _ = workflow_fixture(self.root, Model(clarify=True))
        task = workflow.create(DevelopmentRequest(agent_id, "Java application"))
        await collect(workflow.begin(task.task_id))
        self.assertEqual(
            workflow.service.get(task.task_id).state, S.WAITING_FOR_CLARIFICATION
        )
        with self.assertRaises(DevelopmentFailure):
            workflow.begin(task.task_id)
        workflow.clarify(task.task_id, "A library")
        await collect(workflow.begin(task.task_id))
        self.assertEqual(workflow.service.get(task.task_id).state, S.COMPLETED)
        self.assertEqual(len(model._turns), 1)

    async def test_disconnect_does_not_deadlock_or_cancel_owned_work(self):
        workflow, agent_id, _, _ = workflow_fixture(self.root)
        task = workflow.create(DevelopmentRequest(agent_id, "Java library"))
        turn = workflow.begin(task.task_id)
        events = ag_ui_events(
            turn, thread_id="thread", run_id="run", heartbeat_seconds=1
        )
        await anext(events)
        await events.aclose()
        await asyncio.wait_for(asyncio.shield(turn.task), 5)
        self.assertEqual(workflow.service.get(task.task_id).state, S.COMPLETED)

    async def test_cancel_waits_for_inflight_build_and_prevents_commit(self):
        workspace = Workspace()
        workspace.release.clear()
        workflow, agent_id, _, _ = workflow_fixture(self.root, workspace=workspace)
        task = workflow.create(DevelopmentRequest(agent_id, "Java library"))
        turn = workflow.begin(task.task_id)
        turn.progress.detach()
        await asyncio.wait_for(workspace.entered.wait(), 5)
        workflow.service.cancel(task.task_id)
        self.assertFalse(turn.task.done())
        self.assertEqual(
            workflow.service.agents.get(agent_id).development_task_id, task.task_id
        )
        workspace.release.set()
        with self.assertRaises(AgentLifecycleFailure):
            await turn.task
        self.assertEqual(workflow.service.get(task.task_id).state, S.CANCELLED)
        self.assertNotIn(W.COMMIT, workspace.operations)
        self.assertIsNone(workflow.service.agents.get(agent_id).development_task_id)

    async def test_invalid_files_and_fix_budget_fail_before_commit(self):
        for model, workspace, code in [
            (Model(invalid_file=True), Workspace(), "invalid_model_result"),
            (Model(), Workspace(fail_builds=5), "fix_limit"),
        ]:
            workflow, agent_id, _, _ = workflow_fixture(self.root, model, workspace)
            task = workflow.create(
                DevelopmentRequest(agent_id, "Java library", max_fix_attempts=0)
            )
            events = await collect(workflow.begin(task.task_id))
            self.assertEqual(events[-1]["type"], "RUN_ERROR")
            self.assertNotIn("RUN_FINISHED", [e["type"] for e in events])
            self.assertEqual(workflow.service.get(task.task_id).failure_code, code)
            self.assertNotIn(W.COMMIT, workspace.operations)

    async def test_secret_specification_rejected_before_history_or_workspace(self):
        workflow, agent_id, model, workspace = workflow_fixture(self.root)
        workflow.secrets = ConfiguredSecretPolicy(("private-test-token",))
        with self.assertRaises(DevelopmentFailure):
            workflow.create(DevelopmentRequest(agent_id, "Include private-test-token"))
        self.assertEqual(model.phases, [])
        self.assertEqual(workspace.operations, [])


def native_git(*args):
    result = subprocess.run(
        ["git", *map(str, args)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    if result.returncode:
        raise AssertionError("test fixture Git operation failed")
    return result.stdout.strip()


class LocalWorkspace(Workspace):
    execution_backend = "local-native-git-with-fake-java-build"

    def __init__(self, root, node):
        super().__init__()
        self.root, self.node = root, node

    async def execute(self, agent_id, request):
        if request.operation in {W.TEST, W.PACKAGE}:
            return await super().execute(agent_id, request)
        policy = ConfiguredSecretPolicy()
        result = await asyncio.to_thread(
            subprocess.run,
            [
                self.node,
                str(Path(__file__).with_name("local_workspace_runner.mjs")),
                str(self.root),
                json.dumps(workspace_payload(request, policy)),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode:
            raise AssertionError("test helper failed")
        return workspace_result(result.stdout, policy)


class DevelopmentGitTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_workflow_publishes_real_bare_remote(self):
        node = shutil.which("node") or str(
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        )
        if not shutil.which("git") or not Path(node).is_file():
            self.skipTest("LOCAL_NOT_AVAILABLE: Git or Node")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, agent_id, _, _ = workflow_fixture(
                root, workspace=LocalWorkspace(root, node)
            )

            from tests.test_trusted_git import URL, LocalTrustedGit, seed_remote

            remote = seed_remote(root)
            workflow.git = LocalTrustedGit(root, remote)
            task = workflow.create(
                DevelopmentRequest(
                    agent_id,
                    "Java library",
                    repository_url=URL,
                    base_branch="master",
                    publish=True,
                )
            )
            events = await collect(workflow.begin(task.task_id))
            result = workflow.service.get(task.task_id)
            self.assertEqual(result.state, S.COMPLETED, result.failure_code)
            self.assertTrue(result.result.published)
            self.assertEqual(
                native_git(
                    "--git-dir", remote, "rev-parse", result.result.working_branch
                ),
                result.result.commit_id,
            )
            self.assertIn(
                "int sum",
                native_git(
                    "--git-dir",
                    remote,
                    "show",
                    result.result.working_branch + ":src/main/java/App.java",
                ),
            )
            self.assertEqual(events[-1]["type"], "RUN_FINISHED")
