"""Small application-level event loop for executing an ExecutionGraph."""

import asyncio
from dataclasses import dataclass

from universal_agent_runtime.application.ports.orchestration import (
    WorkerCompletionReader,
    WorkerOrchestrator,
    WorkerResult,
)
from universal_agent_runtime.domain.orchestration import (
    ExecutionGraph,
    WorkerSpec,
    WorkerStatus,
)
from universal_agent_runtime.domain.orchestration_trace import (
    OrchestrationEventKind,
    OrchestrationTrace,
)


@dataclass(frozen=True)
class GraphExecutionResult:
    graph_id: str
    results: tuple[tuple[str, str], ...]
    failures: tuple[tuple[str, str], ...]
    blocked_worker_ids: tuple[str, ...]

    @property
    def successful(self) -> bool:
        return not self.failures and not self.blocked_worker_ids


class ExecutionGraphExecutor:
    """Runs every currently runnable worker concurrently and advances the DAG."""

    def __init__(
        self,
        orchestrator: WorkerOrchestrator,
        completion_reader: WorkerCompletionReader | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.completion_reader = completion_reader or _completion_reader_from(orchestrator)

    async def execute(
        self,
        graph: ExecutionGraph,
        *,
        user_task_id: str | None = None,
        trace: OrchestrationTrace | None = None,
    ) -> GraphExecutionResult:
        if trace is not None and user_task_id is not None and trace.user_task_id != user_task_id:
            raise ValueError("trace belongs to a different user task")
        active: dict[asyncio.Task[WorkerResult], WorkerSpec] = {}
        traced_blocked: set[str] = set()

        def record(kind: OrchestrationEventKind, worker_id: str | None = None) -> None:
            if trace is not None:
                trace.record(kind, worker_id)

        while True:
            self._record_new_blocked(graph, traced_blocked, record)
            for worker in graph.runnable_workers():
                graph.set_status(worker.worker_id, WorkerStatus.RUNNING)
                record(OrchestrationEventKind.WORKER_SUBMITTED, worker.worker_id)
                record(OrchestrationEventKind.WORKER_RUNNING, worker.worker_id)
                active[asyncio.create_task(self._run_worker(worker))] = worker

            if not active:
                if not graph.workers or graph.terminal:
                    break
                raise RuntimeError("execution graph has waiting workers but none are runnable")

            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                worker = active.pop(task)
                try:
                    outcome = task.result()
                except Exception:
                    outcome = WorkerResult(worker.worker_id, failure="worker_execution_failed")
                if outcome.worker_id != worker.worker_id:
                    outcome = WorkerResult(worker.worker_id, failure="worker_result_identity_mismatch")
                if outcome.result is not None:
                    graph.set_status(worker.worker_id, WorkerStatus.COMPLETED, result=outcome.result)
                    record(OrchestrationEventKind.WORKER_COMPLETED, worker.worker_id)
                else:
                    graph.set_status(worker.worker_id, WorkerStatus.FAILED, failure=outcome.failure)
                    record(OrchestrationEventKind.WORKER_FAILED, worker.worker_id)
            self._record_new_blocked(graph, traced_blocked, record)

        return GraphExecutionResult(
            graph_id=graph.graph_id,
            results=tuple((w.worker_id, w.result) for w in graph.completed_workers() if w.result is not None),
            failures=tuple((w.worker_id, w.failure) for w in graph.failed_workers() if w.failure is not None),
            blocked_worker_ids=tuple(w.worker_id for w in graph.blocked_workers()),
        )

    async def _run_worker(self, worker: WorkerSpec) -> WorkerResult:
        try:
            status = await self.orchestrator.submit(worker)
            if status not in {WorkerStatus.COMPLETED, WorkerStatus.FAILED}:
                async for observed in self.orchestrator.watch(worker.worker_id):
                    if observed in {WorkerStatus.COMPLETED, WorkerStatus.FAILED}:
                        status = observed
                        break
                else:
                    status = await self.orchestrator.status(worker.worker_id)
            if status not in {WorkerStatus.COMPLETED, WorkerStatus.FAILED}:
                return WorkerResult(worker.worker_id, failure="worker_did_not_reach_terminal_state")
            if self.completion_reader is None:
                return WorkerResult(worker.worker_id, failure="worker_completion_channel_unavailable")
            outcome = await self.completion_reader.get_result(worker.worker_id)
            if not isinstance(outcome, WorkerResult):
                return WorkerResult(worker.worker_id, failure="invalid_worker_completion_result")
            return outcome
        except Exception:
            return WorkerResult(worker.worker_id, failure="worker_execution_failed")

    @staticmethod
    def _record_new_blocked(graph, traced_blocked, record) -> None:
        for worker in graph.blocked_workers():
            if worker.worker_id not in traced_blocked:
                traced_blocked.add(worker.worker_id)
                record(OrchestrationEventKind.WORKER_BLOCKED, worker.worker_id)


def _completion_reader_from(orchestrator: WorkerOrchestrator) -> WorkerCompletionReader | None:
    candidate = getattr(orchestrator, "get_result", None)
    return orchestrator if callable(candidate) else None
