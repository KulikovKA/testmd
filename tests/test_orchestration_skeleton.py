"""Local orchestration checks requiring no Docker, AX, or Kubernetes service."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from universal_agent_runtime.adapters.ax_orchestrator import (
    AXConfiguration, AXOrchestrator, AXUnavailable, map_worker_to_ax_task,
)
from universal_agent_runtime.adapters.legacy_worker_orchestrator import LegacyWorkerOrchestrator
from universal_agent_runtime.application.static_coordinator import StaticCoordinator
from universal_agent_runtime.configuration import (
    ApplicationSettings, ConfigurationError, OrchestrationBackend, RuntimeDriver,
)
from universal_agent_runtime.domain.orchestration import (
    ExecutionGraph, UserTask, WorkerResources, WorkerSpec, WorkerStatus,
)
from universal_agent_runtime.domain.orchestration_trace import OrchestrationEventKind, OrchestrationTrace
from universal_agent_runtime.domain.agent import AgentLifecycleState
from universal_agent_runtime.domain.identifiers import AgentId
from universal_agent_runtime.domain.message import Message


def worker(name, dependencies=()):
    return WorkerSpec(name, "developer", "do work", dependencies,
                      resources=WorkerResources(1, 1024, 16), workspace_reference=f"{name}-workspace")


def test_graph_dependency_failure_and_completion():
    graph = ExecutionGraph("graph-1")
    for item in (worker("architect"), worker("developer", ("architect",)),
                 worker("tester", ("developer",)), worker("reviewer", ("developer",))):
        graph.add_worker(item)
    assert [w.worker_id for w in graph.runnable_workers()] == ["architect"]
    graph.set_status("architect", WorkerStatus.RUNNING)
    graph.set_status("architect", WorkerStatus.COMPLETED, result="plan")
    assert [w.worker_id for w in graph.runnable_workers()] == ["developer"]
    graph.set_status("developer", WorkerStatus.RUNNING)
    graph.set_status("developer", WorkerStatus.FAILED, failure="compile_failed")
    assert {w.worker_id for w in graph.blocked_workers()} == {"tester", "reviewer"}
    assert graph.terminal and not graph.completed


def test_graph_rejects_cycle_and_completes():
    graph = ExecutionGraph("graph-2")
    graph.add_worker(worker("a"))
    graph.add_worker(worker("b", ("a",)))
    with pytest.raises(ValueError, match="cyclic"):
        graph.add_dependency("a", "b")
    for name in ("a", "b"):
        graph.set_status(name, WorkerStatus.RUNNING)
        graph.set_status(name, WorkerStatus.COMPLETED, result="done")
    assert graph.completed


def test_static_coordinator_output():
    task = UserTask("request-1", "Build a small service")
    graph = StaticCoordinator().plan(task)
    assert [w.role for w in graph.workers] == ["architect", "developer", "tester", "reviewer"]
    assert len({w.workspace_reference for w in graph.workers}) == 4
    assert [w.role for w in graph.runnable_workers()] == ["architect"]


def test_ax_mapping_and_fail_closed():
    config = AXConfiguration("default", "worker-image", ("serve",))
    manifest = map_worker_to_ax_task(worker("worker-1"), config)
    assert manifest["spec"]["workspaces"] == [{"name": "worker-1-workspace", "path": "/workspace"}]
    assert "gateway" not in manifest["spec"]
    assert "env" not in manifest["spec"]
    assert "sandboxClass" not in manifest["spec"]
    with pytest.raises(ValueError, match="microvm"):
        AXConfiguration("default", "worker-image", ("serve",), required_sandbox_class="gvisor")

    class FakeClient:
        called = False

        async def update_task(self, manifest):
            self.called = True

    client = FakeClient()
    adapter = AXOrchestrator(config, client)
    with pytest.raises(AXUnavailable, match="admission"):
        asyncio.run(adapter.submit(worker("worker-1")))
    assert not client.called


def test_ax_pre_admission_and_workspace_are_separate_from_actual_attestation():
    class FakeClient:
        called = False

        async def update_task(self, manifest):
            self.called = True

    class Admission:
        async def verify_admission_policy(self, atespace, spec):
            return atespace == "default" and spec.worker_id == "worker-1"

    class WorkspaceRegistry:
        ready = True

        async def workspace_is_ready(self, atespace, workspace_name):
            return self.ready and atespace == "default" and workspace_name == "worker-1-workspace"

    class Attestor:
        async def attest_task_actor_microvm(self, atespace, task_name):
            return atespace == "default" and task_name == "worker-1"

    client = FakeClient()
    registry = WorkspaceRegistry()
    adapter = AXOrchestrator(AXConfiguration("default", "worker-image", ("serve",)), client,
                             Admission(), Attestor(), registry)

    async def exercise():
        await adapter.verify_pre_admission(worker("worker-1"))
        await adapter.attest_post_launch("worker-1")
        with pytest.raises(AXUnavailable, match="gate worker execution"):
            await adapter.submit(worker("worker-1"))

    asyncio.run(exercise())
    assert not client.called


def test_ax_rejects_unprovisioned_workspace_before_submission():
    class Admission:
        async def verify_admission_policy(self, atespace, spec):
            return True

    class WorkspaceRegistry:
        async def workspace_is_ready(self, atespace, workspace_name):
            return False

    adapter = AXOrchestrator(AXConfiguration("default", "worker-image", ("serve",)),
                             admission_verifier=Admission(), workspaces=WorkspaceRegistry())
    with pytest.raises(AXUnavailable, match="Workspace"):
        asyncio.run(adapter.submit(worker("worker-1")))


def test_ax_submission_requires_post_launch_attestor():
    class FakeClient:
        called = False

        async def update_task(self, manifest):
            self.called = True

    class Admission:
        async def verify_admission_policy(self, atespace, spec):
            return True

    class WorkspaceRegistry:
        async def workspace_is_ready(self, atespace, workspace_name):
            return True

    client = FakeClient()
    adapter = AXOrchestrator(AXConfiguration("default", "worker-image", ("serve",)), client,
                             Admission(), workspaces=WorkspaceRegistry())
    with pytest.raises(AXUnavailable, match="post-launch"):
        asyncio.run(adapter.submit(worker("worker-1")))
    assert not client.called


def test_ax_post_launch_attestation_fails_closed_when_not_verified():
    class Attestor:
        async def attest_task_actor_microvm(self, atespace, task_name):
            return False

    adapter = AXOrchestrator(AXConfiguration("default", "worker-image", ("serve",)),
                             placement_attestor=Attestor())
    with pytest.raises(AXUnavailable, match="actual.*attested"):
        asyncio.run(adapter.attest_post_launch("worker-1"))


def test_legacy_bridge_requires_kata_before_lifecycle_call():
    class FakeLifecycle:
        called = False

        async def create(self, command):
            self.called = True

    lifecycle = FakeLifecycle()
    adapter = LegacyWorkerOrchestrator(lifecycle, object(), RuntimeDriver.DOCKER)
    with pytest.raises(RuntimeError, match="Kata"):
        asyncio.run(adapter.submit(worker("worker-1")))
    assert not lifecycle.called
    assert OrchestrationBackend.LEGACY.value == "legacy"


def test_legacy_bridge_rejects_resource_mismatch():
    adapter = LegacyWorkerOrchestrator(object(), object(), RuntimeDriver.KATA,
                                       WorkerResources(2, 2048, 128))
    with pytest.raises(ValueError, match="resources"):
        asyncio.run(adapter.submit(worker("worker-1")))


def test_legacy_bridge_uses_existing_lifecycle_and_chat():
    class FakeLifecycle:
        commands = []
        state = AgentLifecycleState.READY

        async def create(self, command):
            self.commands.append(command)
            return SimpleNamespace(agent=SimpleNamespace(agent_id=AgentId("agent-1")))

        async def start(self, agent_id):
            assert agent_id == AgentId("agent-1")

        def inspect(self, agent_id):
            return SimpleNamespace(state=self.state)

        async def stop(self, agent_id):
            self.state = AgentLifecycleState.STOPPED

        async def delete(self, agent_id):
            assert self.state is AgentLifecycleState.STOPPED

    class FakeChat:
        async def send(self, agent_id, goal):
            assert goal == "do work"
            return (Message("message-1", "turn-1", 1, "assistant", "done", datetime.now(UTC)),)

    lifecycle = FakeLifecycle()
    adapter = LegacyWorkerOrchestrator(lifecycle, FakeChat(), RuntimeDriver.KATA)

    async def exercise():
        assert await adapter.submit(worker("worker-1")) is WorkerStatus.COMPLETED
        assert await adapter.status("worker-1") is WorkerStatus.COMPLETED
        assert adapter.results["worker-1"] == "done"
        await adapter.delete("worker-1")

    asyncio.run(exercise())
    assert lifecycle.commands[0].request_id == "worker-1"


@pytest.mark.parametrize("failure_point", ["start", "chat", "result"])
def test_legacy_bridge_cleans_failed_attempt_and_allows_retry(failure_point):
    class FakeLifecycle:
        def __init__(self):
            self.commands = []
            self.states = {}
            self.deleted = []

        async def create(self, command):
            self.commands.append(command)
            agent_id = AgentId(f"agent-{len(self.commands)}")
            self.states[agent_id] = AgentLifecycleState.STOPPED
            return SimpleNamespace(agent=SimpleNamespace(agent_id=agent_id))

        async def start(self, agent_id):
            if failure_point == "start" and len(self.commands) == 1:
                self.states[agent_id] = AgentLifecycleState.FAILED
                raise RuntimeError("start failed")
            self.states[agent_id] = AgentLifecycleState.READY

        def inspect(self, agent_id):
            return SimpleNamespace(state=self.states[agent_id])

        async def stop(self, agent_id):
            self.states[agent_id] = AgentLifecycleState.STOPPED

        async def delete(self, agent_id):
            assert self.states[agent_id] is AgentLifecycleState.STOPPED
            self.deleted.append(agent_id)
            del self.states[agent_id]

    class FakeChat:
        def __init__(self):
            self.calls = 0

        async def send(self, agent_id, goal):
            self.calls += 1
            if failure_point == "chat" and self.calls == 1:
                raise RuntimeError("chat failed")
            if failure_point == "result" and self.calls == 1:
                return (Message("message-1", "turn-1", 1, "user", goal, datetime.now(UTC)),)
            return (Message("message-2", "turn-2", 2, "assistant", "done", datetime.now(UTC)),)

    lifecycle = FakeLifecycle()
    adapter = LegacyWorkerOrchestrator(lifecycle, FakeChat(), RuntimeDriver.KATA)

    async def exercise():
        with pytest.raises(RuntimeError):
            await adapter.submit(worker("worker-1"))
        assert AgentId("agent-1") in lifecycle.deleted
        assert "worker-1" not in adapter._agents
        assert "worker-1" not in adapter.results
        assert await adapter.submit(worker("worker-1")) is WorkerStatus.COMPLETED

    asyncio.run(exercise())
    assert lifecycle.commands[0].request_id != lifecycle.commands[1].request_id


def test_legacy_bridge_retries_cleanup_when_initial_cleanup_fails():
    class FakeLifecycle:
        def __init__(self):
            self.states = {}
            self.creates = 0
            self.stop_calls = 0

        async def create(self, command):
            self.creates += 1
            agent_id = AgentId(f"agent-{self.creates}")
            self.states[agent_id] = AgentLifecycleState.STOPPED
            return SimpleNamespace(agent=SimpleNamespace(agent_id=agent_id))

        async def start(self, agent_id):
            if self.creates == 1:
                self.states[agent_id] = AgentLifecycleState.FAILED
                raise RuntimeError("start failed")
            self.states[agent_id] = AgentLifecycleState.READY

        def inspect(self, agent_id):
            return SimpleNamespace(state=self.states[agent_id])

        async def stop(self, agent_id):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("temporary stop failure")
            self.states[agent_id] = AgentLifecycleState.STOPPED

        async def delete(self, agent_id):
            assert self.states[agent_id] is AgentLifecycleState.STOPPED
            del self.states[agent_id]

    class FakeChat:
        async def send(self, agent_id, goal):
            return (Message("message-1", "turn-1", 1, "assistant", "done", datetime.now(UTC)),)

    lifecycle = FakeLifecycle()
    adapter = LegacyWorkerOrchestrator(lifecycle, FakeChat(), RuntimeDriver.KATA)

    async def exercise():
        with pytest.raises(RuntimeError, match="start failed"):
            await adapter.submit(worker("worker-1"))
        assert "worker-1" in adapter._agents
        assert adapter._statuses["worker-1"] is WorkerStatus.FAILED
        assert await adapter.submit(worker("worker-1")) is WorkerStatus.COMPLETED
        assert "worker-1" not in adapter._agents

    asyncio.run(exercise())
    assert lifecycle.creates == 2


def test_backend_defaults_to_legacy_and_ax_needs_explicit_atespace():
    from tests.test_runtime_driver import _environment

    values = _environment("kata")
    assert ApplicationSettings.from_environment(values).orchestration_backend is OrchestrationBackend.LEGACY
    with pytest.raises(ConfigurationError, match="atespace"):
        ApplicationSettings.from_environment({**values, "UAR_ORCHESTRATION_BACKEND": "ax"})
    ax = ApplicationSettings.from_environment({
        **values, "UAR_ORCHESTRATION_BACKEND": "ax", "UAR_AX_ATESPACE": "default",
    })
    assert ax.orchestration_backend is OrchestrationBackend.AX


def test_orchestration_trace_is_bounded_and_separate():
    trace = OrchestrationTrace("request-1", max_events=2)
    trace.record(OrchestrationEventKind.USER_TASK_CREATED)
    trace.record(OrchestrationEventKind.WORKER_RUNNING, "worker-1")
    assert [event.sequence for event in trace.events] == [1, 2]
    with pytest.raises(ValueError, match="limit"):
        trace.record(OrchestrationEventKind.USER_TASK_COMPLETED)
