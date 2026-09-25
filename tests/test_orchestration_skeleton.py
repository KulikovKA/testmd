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
    with pytest.raises(AXUnavailable, match="microvm"):
        asyncio.run(adapter.submit(worker("worker-1")))
    assert not client.called


def test_ax_fake_verified_boundary():
    class FakeClient:
        manifest = None

        async def update_task(self, manifest):
            self.manifest = manifest

    class FakeVerifier:
        async def verified_microvm(self, atespace, spec):
            return atespace == "default" and spec.worker_id == "worker-1"

    client = FakeClient()
    adapter = AXOrchestrator(AXConfiguration("default", "worker-image", ("serve",)), client, FakeVerifier())
    assert asyncio.run(adapter.submit(worker("worker-1"))) is WorkerStatus.WAITING
    assert client.manifest["kind"] == "Task"


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
