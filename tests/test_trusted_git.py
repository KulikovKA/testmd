import asyncio
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from tests.test_development_workflow import collect, native_git, workflow_fixture
from universal_agent_runtime.adapters.docker_development_workspace import (
    workspace_payload,
)
from universal_agent_runtime.adapters.docker_trusted_git import (
    DockerTrustedGitAdapter,
    TrustedGitSettings,
)
from universal_agent_runtime.adapters.repository_access import ConfiguredSecretPolicy
from universal_agent_runtime.adapters.trusted_git_helper import (
    GitCommands,
    copy_tree,
    execute,
    git_failure,
)
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceOperation as W,
)
from universal_agent_runtime.application.ports.development_workspace import (
    WorkspaceRequest,
)
from universal_agent_runtime.application.ports.trusted_git import (
    GitRequest,
    validate_repository_url,
)
from universal_agent_runtime.domain.development_task import (
    DevelopmentFailure,
    DevelopmentRequest,
    RepositoryTarget,
    validate_branch,
)
from universal_agent_runtime.domain.identifiers import AgentId

URL = "ssh://git@10.228.84.126:30022/test/test.git"
ENDPOINTS = ("10.228.84.126:30022",)
REQUEST = GitRequest("task-one", URL, "master", "uar/task-one")


def seed_remote(root):
    remote, seed = root / "remote.git", root / "seed"
    native_git("init", "--bare", "--initial-branch=master", remote)
    native_git("init", "--initial-branch=master", seed)
    (seed / "README.md").write_text("Existing project\n", encoding="utf-8")
    native_git("-C", seed, "add", "README.md")
    native_git(
        "-C",
        seed,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Initial repository",
    )
    native_git("-C", seed, "push", str(remote), "master")
    return remote


class LocalTrustedGit:
    """Explicit test adapter: no local transport is reachable through HTTP/CLI."""

    def __init__(self, root, remote):
        self.root, self.remote = root, remote

    def validate(self, url):
        validate_repository_url(url, ENDPOINTS)

    async def clone(self, agent, request):
        await asyncio.to_thread(
            execute,
            "clone",
            request,
            self.root,
            git=GitCommands(local_test=True),
            remote=str(self.remote),
        )

    async def push(self, agent, request):
        await asyncio.to_thread(
            execute,
            "push",
            request,
            self.root,
            git=GitCommands(local_test=True),
            remote=str(self.remote),
        )


class FakeTrustedGit:
    def __init__(self, failure=None):
        self.failure, self.calls = failure, []
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    def validate(self, url):
        validate_repository_url(url, ENDPOINTS)

    async def clone(self, agent, request):
        self.calls.append(("clone", request))
        self.entered.set()
        await self.release.wait()
        if self.failure and self.failure != "repository_push_failed":
            raise DevelopmentFailure(self.failure)

    async def push(self, agent, request):
        self.calls.append(("push", request))
        if self.failure:
            raise DevelopmentFailure(self.failure)


class RepositoryValidationTests(unittest.TestCase):
    def test_valid_url_and_endpoint_pair(self):
        self.assertEqual(validate_repository_url(URL, ENDPOINTS), URL)
        for value in (URL.replace("84.126", "84.127"), URL.replace(":30022", ":22")):
            with self.assertRaises(DevelopmentFailure) as error:
                validate_repository_url(value, ENDPOINTS)
            self.assertEqual(error.exception.code, "repository_not_allowed")

    def test_invalid_urls_never_echo_input(self):
        for value in (
            URL.replace("git@", "root@"),
            URL.replace("git@", "git:SECRET@"),
            URL + "?token=SECRET",
            URL + "#x",
            URL + "?",
            URL + "#",
            URL.replace("test/test.git", "../test.git"),
            URL.replace("test/test.git", "a//test.git"),
            URL.replace("test/test.git", "a/%2e%2e/test.git"),
            URL.replace(":30022", ":99999"),
            URL + ";cmd",
            URL + "\n",
            "git@host:repo.git",
            "file:///tmp/repo.git",
            "/tmp/repo.git",
            "https://example.org/repo.git",
            URL.replace("/test/test.git", "/-option/test.git"),
        ):
            with (
                self.subTest(value=value),
                self.assertRaises(DevelopmentFailure) as error,
            ):
                validate_repository_url(value, ENDPOINTS)
            self.assertEqual(str(error.exception), "repository_url_invalid")

    def test_branches_are_validated_and_task_branch_is_deterministic(self):
        for branch in ("-f", "../master", "x.lock", "a//b", "a@{b}", "x\n", "HEAD"):
            with self.assertRaises(ValueError):
                validate_branch(branch)
        with self.assertRaises(DevelopmentFailure):
            replace(REQUEST, working_branch="master")
        with self.assertRaises(ValueError):
            replace(REQUEST, base_branch="--force")
        self.assertEqual(REQUEST.working_branch, "uar/task-one")

    def test_safe_error_mapping(self):
        for text, expected in [
            ("Permission denied PRIVATE", "repository_auth_failed"),
            ("Host key verification failed SECRET", "repository_unavailable"),
            ("Remote branch missing not found", "repository_branch_not_found"),
            ("Repository not found", "repository_not_found"),
            ("[rejected] non-fast-forward", "repository_conflict"),
            ("SECRET", "repository_push_failed"),
        ]:
            self.assertEqual(str(git_failure(text, "repository_push_failed")), expected)

    def test_agent_remote_operations_are_rejected(self):
        for operation in (W.CLONE, W.PUSH):
            with self.assertRaises(DevelopmentFailure):
                workspace_payload(
                    WorkspaceRequest("task-one", operation), ConfiguredSecretPolicy()
                )


class TrustedWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_repository_disconnect_completes_owned_work_without_publication(self):
        from universal_agent_runtime.ag_ui import ag_ui_events

        with tempfile.TemporaryDirectory() as directory:
            workflow, agent, _, _ = workflow_fixture(Path(directory))
            git = workflow.git = FakeTrustedGit()
            git.release.clear()
            task = workflow.create(
                DevelopmentRequest(agent, "Java", repository_url=URL, publish=False)
            )
            turn = workflow.begin(task.task_id)
            events = ag_ui_events(turn, thread_id="t", run_id="r", heartbeat_seconds=1)
            await anext(events)
            await events.aclose()
            await asyncio.wait_for(git.entered.wait(), 2)
            git.release.set()
            await asyncio.wait_for(turn.task, 5)
            self.assertFalse(workflow.service.get(task.task_id).result.published)
            self.assertEqual([call[0] for call in git.calls], ["clone"])

    async def test_success_failure_trace_and_no_credential_payloads(self):
        for failure in (
            None,
            "repository_auth_failed",
            "repository_branch_not_found",
            "repository_push_failed",
        ):
            with tempfile.TemporaryDirectory() as directory:
                workflow, agent, model, workspace = workflow_fixture(Path(directory))
                git = workflow.git = FakeTrustedGit(failure)
                task = workflow.create(
                    DevelopmentRequest(
                        agent, "Java library", repository_url=URL, publish=True
                    )
                )
                events = await collect(workflow.begin(task.task_id))
                task = workflow.service.get(task.task_id)
                self.assertEqual(task.failure_code, failure)
                self.assertNotIn(W.CLONE, workspace.operations)
                self.assertNotIn(W.PUSH, workspace.operations)
                if failure is None:
                    self.assertTrue(task.result.published)
                    self.assertEqual(task.result.working_branch, "uar/" + task.task_id)
                    self.assertEqual(task.result.base_branch, "master")
                    self.assertEqual(task.result.repository_url, URL)
                    self.assertEqual([c[0] for c in git.calls], ["clone", "push"])
                    self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                    names = [e.type for e in task.trace]
                    for name in (
                        "repository_clone_started",
                        "repository_clone_finished",
                        "branch_created",
                        "git_commit",
                        "repository_push_started",
                        "repository_push_finished",
                    ):
                        self.assertIn(name, names)
                    self.assertLess(
                        names.index("repository_clone_finished"),
                        names.index("llm_turn_started"),
                    )
                else:
                    self.assertEqual(events[-1]["type"], "RUN_ERROR")
                public = json.dumps(events) + repr(model._turns) + repr(git.calls)
                for secret in (
                    "PRIVATE KEY",
                    "private_key",
                    "known_hosts",
                    "GIT_SSH_COMMAND",
                ):
                    self.assertNotIn(secret, public)

    async def test_legacy_creation_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow, agent, _, _ = workflow_fixture(Path(directory))
            with self.assertRaises(DevelopmentFailure):
                workflow.create(
                    DevelopmentRequest(
                        agent, "Java", repository=RepositoryTarget("team", "repo")
                    )
                )

    async def test_cancel_during_clone_waits_and_does_not_start_model_or_push(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow, agent, model, _ = workflow_fixture(Path(directory))
            git = workflow.git = FakeTrustedGit()
            git.release.clear()
            task = workflow.create(
                DevelopmentRequest(agent, "Java", repository_url=URL, publish=True)
            )
            turn = workflow.begin(task.task_id)
            turn.progress.detach()
            await asyncio.wait_for(git.entered.wait(), 2)
            workflow.service.cancel(task.task_id)
            self.assertFalse(turn.task.done())
            git.release.set()
            await asyncio.gather(turn.task, return_exceptions=True)
            self.assertEqual(
                workflow.service.get(task.task_id).failure_code, "cancelled"
            )
            self.assertEqual(model.phases, [])
            self.assertEqual(len(git.calls), 1)


class DockerHelperTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, root, *, failure=None):
        key, known = root / "synthetic-key", root / "synthetic-known-hosts"
        key.write_text("SYNTHETIC-PRIVATE-FIXTURE", encoding="utf-8")
        known.write_text("synthetic-host-key", encoding="utf-8")
        sequence = []
        agent = Mock(
            status="running",
            attrs={
                "Mounts": [
                    {
                        "Type": "volume",
                        "Destination": "/workspace",
                        "Name": "owned-volume",
                    }
                ]
            },
        )
        agent.pause.side_effect = lambda: sequence.append("pause")
        agent.unpause.side_effect = lambda: sequence.append("unpause")
        helper = Mock()
        helper.start.side_effect = lambda: sequence.append("start")
        helper.remove.side_effect = lambda **kw: sequence.append("remove")
        helper.wait.return_value = {"StatusCode": 1 if failure else 0}
        helper.logs.return_value = json.dumps(
            {"success": not failure, "error": failure}
        ).encode()
        client = Mock()
        client.containers.list.return_value = [agent]
        client.containers.create.return_value = helper
        adapter = DockerTrustedGitAdapter(
            client, TrustedGitSettings(str(key), str(known), ENDPOINTS)
        )
        return adapter, client, helper, sequence

    async def test_key_only_mounted_in_helper_cleanup_success_failure_and_timeout(self):
        for failure in (None, "repository_auth_failed", "timeout"):
            with tempfile.TemporaryDirectory() as directory:
                adapter, client, helper, sequence = self.adapter(
                    Path(directory), failure=failure
                )
                if failure == "timeout":
                    helper.wait.side_effect = TimeoutError("PRIVATE diagnostic")
                if failure:
                    with self.assertRaises(DevelopmentFailure) as error:
                        await adapter.clone(AgentId("agent-one"), REQUEST)
                    self.assertNotIn("PRIVATE", str(error.exception))
                else:
                    await adapter.clone(AgentId("agent-one"), REQUEST)
                self.assertEqual(sequence, ["pause", "start", "remove", "unpause"])
                args, kwargs = client.containers.create.call_args
                self.assertNotIn("synthetic-key", repr(args))
                self.assertNotIn("environment", kwargs)
                mounts = kwargs["volumes"]
                self.assertEqual(
                    mounts[str(Path(directory) / "synthetic-key")],
                    {"bind": "/run/uar/key", "mode": "ro"},
                )
                self.assertTrue(kwargs["read_only"])
                self.assertNotIn("privileged", kwargs)
                # Validate against the installed Docker SDK without daemon access.
                from docker.models.containers import _create_container_args

                converted = _create_container_args({**kwargs, "version": "1.45"})
                self.assertTrue(converted["host_config"]["ReadonlyRootfs"])

    async def test_lost_create_response_still_removes_named_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, client, helper, sequence = self.adapter(Path(directory))
            client.containers.create.side_effect = TimeoutError("response lost")
            client.containers.get.return_value = helper
            with self.assertRaises(DevelopmentFailure):
                await adapter.clone(AgentId("agent-one"), REQUEST)
            name = client.containers.create.call_args.kwargs["name"]
            client.containers.get.assert_called_once_with(name)
            self.assertEqual(sequence, ["pause", "remove", "unpause"])

    async def test_cancel_waits_for_helper_removal_before_unpause(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, _, helper, sequence = self.adapter(Path(directory))
            entered, release = threading.Event(), threading.Event()

            def wait(**kwargs):
                entered.set()
                release.wait(5)
                return {"StatusCode": 0}

            helper.wait.side_effect = wait
            task = asyncio.create_task(adapter.clone(AgentId("agent-one"), REQUEST))
            await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(sequence[-2:], ["remove", "unpause"])

    async def test_cleanup_failure_keeps_agent_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, _, helper, sequence = self.adapter(Path(directory))
            helper.remove.side_effect = RuntimeError("cannot remove")
            with self.assertRaises(DevelopmentFailure):
                await adapter.clone(AgentId("agent-one"), REQUEST)
            self.assertNotIn("unpause", sequence)

    async def test_missing_known_hosts_fail_before_helper_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, client, _, _ = self.adapter(Path(directory))
            (Path(directory) / "synthetic-known-hosts").unlink()
            with self.assertRaises(DevelopmentFailure):
                await adapter.clone(AgentId("agent-one"), REQUEST)
            client.containers.create.assert_not_called()


class NativeHelperTests(unittest.TestCase):
    def test_helper_image_python_payload_runs_without_api_dependencies(self):
        import os
        import shutil
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = Path(__file__).resolve().parents[1]
            for line in (repo / "git_helper/Dockerfile").read_text().splitlines():
                if not line.startswith("COPY "):
                    continue
                _, *sources, destination = line.split()
                target = root / destination.removeprefix("/opt/")
                target.mkdir(parents=True, exist_ok=True)
                for source in sources:
                    shutil.copyfile(repo / source, target / Path(source).name)
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-m",
                    "universal_agent_runtime.adapters.trusted_git_helper",
                ],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(root)},
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(
                json.loads(result.stdout),
                {"success": False, "error": "invalid_request"},
            )
            self.assertEqual(result.stderr, "")

    def test_ssh_command_pins_hosts_and_clears_ambient_credentials(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:  # noqa: SIM117 - fixture lifetime
            with patch(
                "universal_agent_runtime.adapters.trusted_git_helper.subprocess.run"
            ) as run:
                run.return_value.returncode = 0
                GitCommands()(Path(directory), "ls-remote", "--", URL)
                args, kwargs = run.call_args
                env = kwargs["env"]
                self.assertIn("StrictHostKeyChecking=yes", env["GIT_SSH_COMMAND"])
                self.assertIn(
                    "UserKnownHostsFile=/run/uar/known_hosts", env["GIT_SSH_COMMAND"]
                )
                self.assertIn("IdentityAgent=none", env["GIT_SSH_COMMAND"])
                self.assertNotIn("SSH_AUTH_SOCK", env)
                self.assertNotIn("OPENAI_API_KEY", env)
                self.assertNotIn("protocol.file.allow=always", args[0])
                self.assertFalse(kwargs.get("shell", False))

    def test_divergent_remote_branch_fails_without_force_or_base_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = seed_remote(root)
            git = GitCommands(local_test=True)
            execute("clone", REQUEST, root, git=git, remote=str(remote))
            project = root / "projects/task-one"
            (project / "task.txt").write_text("task", encoding="utf-8")
            native_git("-C", project, "add", "task.txt")
            native_git(
                "-C",
                project,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "task",
            )
            commit = native_git("-C", project, "rev-parse", "HEAD")
            seed = root / "seed"
            native_git("-C", seed, "checkout", "-b", REQUEST.working_branch)
            (seed / "concurrent.txt").write_text("concurrent", encoding="utf-8")
            native_git("-C", seed, "add", "concurrent.txt")
            native_git(
                "-C",
                seed,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "concurrent",
            )
            native_git("-C", seed, "push", str(remote), REQUEST.working_branch)
            before = native_git(
                "--git-dir", remote, "rev-parse", REQUEST.working_branch
            )
            with self.assertRaises(DevelopmentFailure) as error:
                execute(
                    "push",
                    replace(REQUEST, commit_id=commit),
                    root,
                    git=git,
                    remote=str(remote),
                )
            self.assertEqual(error.exception.code, "repository_conflict")
            self.assertEqual(
                native_git("--git-dir", remote, "rev-parse", REQUEST.working_branch),
                before,
            )

    def test_existing_remote_commit_push_ignores_agent_config_hooks_and_alternates(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = seed_remote(root)
            commands = GitCommands(local_test=True)
            execute("clone", REQUEST, root, git=commands, remote=str(remote))
            project = root / "projects/task-one"
            self.assertEqual(
                native_git("-C", project, "branch", "--show-current"),
                REQUEST.working_branch,
            )
            (project / "README.md").write_text("Updated project\n", encoding="utf-8")
            native_git("-C", project, "add", "README.md")
            native_git(
                "-C",
                project,
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-m",
                "Task",
            )
            commit = native_git("-C", project, "rev-parse", "HEAD")
            (project / ".git/config").write_text(
                "[include]\npath=/run/uar/key\n[core]\nsshCommand=malicious\n",
                encoding="utf-8",
            )
            (project / ".git/objects/info/alternates").write_text(
                "/run/uar/key\n", encoding="utf-8"
            )
            hook = project / ".git/hooks/pre-push"
            hook.parent.mkdir(exist_ok=True)
            hook.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            hook.chmod(0o755)
            calls = []

            def record(cwd, *args, **kwargs):
                calls.append(args)
                return commands(cwd, *args, **kwargs)

            execute(
                "push",
                replace(REQUEST, commit_id=commit),
                root,
                git=record,
                remote=str(remote),
            )
            self.assertEqual(
                native_git("--git-dir", remote, "rev-parse", REQUEST.working_branch),
                commit,
            )
            self.assertNotEqual(
                native_git("--git-dir", remote, "rev-parse", "master"), commit
            )
            push = next(c for c in calls if c[0] == "push")
            self.assertFalse(
                any(a.startswith(("+", "--force", "--delete")) for a in push)
            )
            self.assertEqual(push[-1], commit + ":refs/heads/uar/task-one")

    def test_missing_base_branch_has_stable_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = seed_remote(root)
            with self.assertRaises(DevelopmentFailure) as error:
                execute(
                    "clone",
                    replace(REQUEST, base_branch="missing"),
                    root,
                    git=GitCommands(local_test=True),
                    remote=str(remote),
                )
            self.assertEqual(error.exception.code, "repository_branch_not_found")

    def test_object_copy_rejects_hardlinks(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "objects"
            (source / "aa").mkdir(parents=True)
            secret = root / "synthetic"
            secret.write_text("fixture", encoding="utf-8")
            os.link(secret, source / "aa" / ("a" * 38))
            with self.assertRaises(DevelopmentFailure):
                copy_tree(source, root / "destination", objects=True)


class ExistingRepositoryHttpTests(unittest.TestCase):
    def test_public_contract_trace_and_native_llm_turns_exclude_ssh_credentials(self):
        import re

        from fastapi.testclient import TestClient

        from tests.test_ag_ui import AGUIHttpTests, _events
        from tests.test_development_workflow import Workspace
        from universal_agent_runtime.adapters.qwen_session import (
            QwenExecution,
            QwenSessionAdapter,
            QwenSessionConfig,
        )
        from universal_agent_runtime.application.development_workflow import JAVA_SKILLS
        from universal_agent_runtime.composition import compose_application
        from universal_agent_runtime.http_api import create_application

        class Runner:
            def __init__(self):
                self.invocations = []

            def run(self, invocation):
                self.invocations.append(invocation)
                phase = re.findall(r"DEVELOPMENT_PHASE: ([A-Z_]+)", invocation.prompt)[
                    -1
                ]
                value = {
                    "ANALYZING_REQUIREMENTS": {"questions": []},
                    "PLANNING": {"steps": ["Implement and test"]},
                    "IMPLEMENTING": {
                        "files": [{"path": "pom.xml", "content": "<project/>"}]
                    },
                    "REVIEWING": {"approved": True, "findings": []},
                }[phase]
                answer = json.dumps(value)
                transcript = (
                    invocation.qwen_home
                    / "projects/workspace/chats"
                    / (str(invocation.native_session_id) + ".jsonl")
                )
                transcript.parent.mkdir(parents=True, exist_ok=True)
                with transcript.open("a", encoding="utf-8") as stream:
                    for role, content in (
                        ("user", invocation.prompt),
                        ("assistant", answer),
                    ):
                        stream.write(
                            json.dumps(
                                {
                                    "type": role,
                                    "message": {
                                        "role": role,
                                        "parts": [{"text": content}],
                                    },
                                }
                            )
                            + "\n"
                        )
                return QwenExecution(invocation.native_session_id, answer)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = AGUIHttpTests()._app(root / "app").state.composition
            runner, git = Runner(), FakeTrustedGit()
            adapter = QwenSessionAdapter(
                QwenSessionConfig(
                    storage_root=root / "sessions",
                    base_url="http://ollama.example/v1",
                    model="test",
                    api_key="synthetic-api-secret",
                ),
                runner=runner,
            )
            settings = replace(
                original.settings,
                git_ssh_private_key_file=str(root / "synthetic-private-key"),
                git_ssh_known_hosts_file=str(root / "synthetic-known-hosts"),
            )
            composition = compose_application(
                settings,
                runtime=original.runtime,
                interaction=adapter,
                development_workspace=Workspace(),
                trusted_git=git,
            )
            with TestClient(create_application(composition)) as client:
                agent = client.post(
                    "/agents",
                    json={
                        "request_id": "repo-agent",
                        "skills": list(JAVA_SKILLS),
                        "tools": [],
                    },
                ).json()["agent_id"]
                self.assertEqual(client.post(f"/agents/{agent}/start").status_code, 200)
                endpoint = f"/agents/{agent}/development-tasks"
                request = {
                    "specification": "Java library",
                    "repository_url": URL,
                    "base_branch": "master",
                    "publish": True,
                }
                for invalid in (
                    {"repository_url": URL.replace("84.126", "84.127")},
                    {"base_branch": "--force"},
                    {"repository_url": URL + "?secret=private"},
                    {"local_only": True},
                ):
                    response = client.post(endpoint, json={**request, **invalid})
                    self.assertEqual(response.status_code, 422)
                    self.assertNotIn("secret=private", response.text)
                self.assertEqual(
                    client.post(
                        endpoint, json={"specification": "local without explicit mode"}
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    client.post(
                        endpoint,
                        json={
                            "specification": "legacy",
                            "repository": {"namespace": "x", "name": "y"},
                        },
                    ).status_code,
                    422,
                )
                response = client.post(endpoint, json=request)
                self.assertEqual(response.status_code, 201, response.text)
                task_id = response.json()["task_id"]
                response = client.post(
                    f"/ag-ui/development-tasks/{task_id}/run",
                    json={"threadId": "t", "runId": "r"},
                )
                events = _events(response.text)
                self.assertEqual(events[-1]["type"], "RUN_FINISHED", response.text)
                result = client.get(f"/development-tasks/{task_id}").json()["result"]
                self.assertTrue(result["published"])
                self.assertEqual(result["working_branch"], "uar/" + task_id)
                trace = client.get(f"/development-tasks/{task_id}/trace").text
                turns = client.get(f"/agents/{agent}/llm-turns")
                self.assertEqual(turns.status_code, 200, turns.text)
                self.assertEqual(len(turns.json()["turns"]), 4)
                public = response.text + trace + turns.text + repr(runner.invocations)
                for value in (
                    settings.git_ssh_private_key_file,
                    settings.git_ssh_known_hosts_file,
                    "SYNTHETIC-PRIVATE-FIXTURE",
                    "GIT_SSH_COMMAND",
                ):
                    self.assertNotIn(value, public)
