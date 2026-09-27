"""Application graph executor tests, independent of AX and Kubernetes."""

import asyncio

from universal_agent_runtime.application.execution_graph_executor import ExecutionGraphExecutor
from universal_agent_runtime.application.ports.orchestration import WorkerResult
from universal_agent_runtime.application.static_coordinator import StaticCoordinator
from universal_agent_runtime.domain.orchestration import (
    ExecutionGraph,
    UserTask,
    WorkerSpec,
    WorkerStatus,
)
from universal_agent_runtime.domain.orchestration_trace import (
    OrchestrationEventKind,
    OrchestrationTrace,
)


class FakeWorkerOrchestrator:
    def __init__(self, *, fail_role: str | None = None, parallel_roles: set[str] | None = None):
        self.fail_role = fail_role
        self.parallel_roles = parallel_roles or set()
        self.submitted: list[str] = []
        self.results: dict[str, WorkerResult] = {}
        self.statuses: dict[str, WorkerStatus] = {}
        self.parallel_started: set[str] = set()
        self.parallel_release = asyncio.Event()

    async def submit(self, worker: WorkerSpec) -> WorkerStatus:
        self.submitted.append(worker.role)
        self.statuses[worker.worker_id] = WorkerStatus.RUNNING
        if worker.role in self.parallel_roles:
            self.parallel_started.add(worker.role)
            if self.parallel_started == self.parallel_roles:
                self.parallel_release.set()
            await self.parallel_release.wait()
        if worker.role == self.fail_role:
            self.statuses[worker.worker_id] = WorkerStatus.FAILED
            self.results[worker.worker_id] = WorkerResult(worker.worker_id, failure="test_failure")
            return WorkerStatus.FAILED
        self.statuses[worker.worker_id] = WorkerStatus.COMPLETED
        self.results[worker.worker_id] = WorkerResult(worker.worker_id, result=f"result:{worker.role}")
        return WorkerStatus.COMPLETED

    async def get_result(self, worker_id: str) -> WorkerResult:
        return self.results[worker_id]

    async def status(self, worker_id: str) -> WorkerStatus:
        return self.statuses[worker_id]

    async def watch(self, worker_id: str):
        yield await self.status(worker_id)

    async def suspend(self, worker_id: str) -> WorkerStatus:
        raise NotImplementedError

    async def resume(self, worker_id: str) -> WorkerStatus:
        raise NotImplementedError

    async def delete(self, worker_id: str) -> None:
        self.statuses.pop(worker_id, None)


def _planned_graph():
    return StaticCoordinator().plan(UserTask("request-graph", "Build and verify a service"))


def test_executor_runs_dag_in_order_and_fans_out_concurrently_with_results_and_trace():
    async def exercise():
        fake = FakeWorkerOrchestrator(parallel_roles={"tester", "reviewer"})
        graph = _planned_graph()
        trace = OrchestrationTrace("request-graph")
        outcome = await asyncio.wait_for(
            ExecutionGraphExecutor(fake).execute(graph, trace=trace), timeout=1
        )
        return fake, graph, outcome, trace

    fake, graph, outcome, trace = asyncio.run(exercise())
    assert fake.submitted[0] == "architect"
    assert fake.submitted[1] == "developer"
    assert set(fake.submitted[2:]) == {"tester", "reviewer"}
    assert fake.parallel_started == {"tester", "reviewer"}
    assert graph.completed
    assert {w.result for w in graph.completed_workers()} == {
        "result:architect", "result:developer", "result:tester", "result:reviewer"
    }
    assert outcome.successful
    assert len(outcome.results) == 4
    kinds = [event.kind for event in trace.events]
    assert OrchestrationEventKind.WORKER_SUBMITTED in kinds
    assert OrchestrationEventKind.WORKER_RUNNING in kinds
    assert kinds.count(OrchestrationEventKind.WORKER_COMPLETED) == 4


def test_failed_dependency_blocks_descendants_without_submission():
    async def exercise():
        fake = FakeWorkerOrchestrator(fail_role="developer")
        graph = _planned_graph()
        trace = OrchestrationTrace("request-graph")
        outcome = await ExecutionGraphExecutor(fake).execute(graph, trace=trace)
        return fake, graph, outcome, trace

    fake, graph, outcome, trace = asyncio.run(exercise())
    assert fake.submitted == ["architect", "developer"]
    assert {w.role for w in graph.blocked_workers()} == {"tester", "reviewer"}
    assert not outcome.successful
    assert len(outcome.failures) == 1
    assert {event.worker_id for event in trace.events if event.kind is OrchestrationEventKind.WORKER_BLOCKED} == {
        w.worker_id for w in graph.blocked_workers()
    }


def test_empty_and_already_terminal_graph_return_without_submission():
    async def exercise():
        fake = FakeWorkerOrchestrator()
        executor = ExecutionGraphExecutor(fake)
        empty = ExecutionGraph("empty-graph")
        empty_result = await executor.execute(empty)
        terminal = ExecutionGraph("terminal-graph")
        terminal.add_worker(WorkerSpec("done", "reviewer", "review", workspace_reference="done-workspace"))
        terminal.set_status("done", WorkerStatus.RUNNING)
        terminal.set_status("done", WorkerStatus.COMPLETED, result="already done")
        terminal_result = await executor.execute(terminal)
        return fake, empty_result, terminal_result

    fake, empty_result, terminal_result = asyncio.run(exercise())
    assert fake.submitted == []
    assert empty_result.successful and empty_result.results == ()
    assert terminal_result.results == (("done", "already done"),)
