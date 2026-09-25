"""Optional worker bridge through the existing Agent lifecycle and chat turn."""

from collections.abc import AsyncIterator
from uuid import uuid4

from universal_agent_runtime.application.agent_chat import AgentChatService
from universal_agent_runtime.application.agent_lifecycle import AgentLifecycleService, CreateAgentCommand
from universal_agent_runtime.configuration import RuntimeDriver
from universal_agent_runtime.domain.orchestration import WorkerResources, WorkerSpec, WorkerStatus
from universal_agent_runtime.domain.agent import AgentLifecycleState


class LegacyWorkerOrchestrator:
    """Uses one lifecycle-allocated workspace per worker; never replaces DockerRuntime."""

    def __init__(self, lifecycle: AgentLifecycleService, chat: AgentChatService,
                 runtime_driver: RuntimeDriver,
                 configured_resources: WorkerResources | None = None) -> None:
        self.lifecycle, self.chat = lifecycle, chat
        self.runtime_driver = runtime_driver
        self.configured_resources = configured_resources
        self._agents = {}
        self._statuses: dict[str, WorkerStatus] = {}
        self.results: dict[str, str] = {}

    async def submit(self, worker: WorkerSpec) -> WorkerStatus:
        if self.runtime_driver is not RuntimeDriver.KATA:
            raise RuntimeError("worker execution requires Kata")
        if self.configured_resources is not None and worker.resources != self.configured_resources:
            raise ValueError("worker resources must match the configured legacy runtime")
        if worker.worker_id in self._agents:
            if self._statuses.get(worker.worker_id) is not WorkerStatus.FAILED:
                raise ValueError("worker already submitted")
            # A previous cleanup may itself have failed. Retry cleanup before
            # allocating another Agent with this logical worker ID.
            await self._cleanup_agent(worker.worker_id)
        self.results.pop(worker.worker_id, None)
        request_id = f"worker-{uuid4().hex}"
        created = await self.lifecycle.create(CreateAgentCommand(request_id, worker.skills))
        agent_id = created.agent.agent_id
        self._agents[worker.worker_id] = agent_id
        try:
            await self.lifecycle.start(agent_id)
            self._statuses[worker.worker_id] = WorkerStatus.RUNNING
            messages = await self.chat.send(agent_id, worker.goal)
            result = next((m.content for m in reversed(messages) if m.role == "assistant"), None)
            if not result:
                raise RuntimeError("worker returned no result")
            self.results[worker.worker_id] = result
            self._statuses[worker.worker_id] = WorkerStatus.COMPLETED
        except BaseException:
            self._statuses[worker.worker_id] = WorkerStatus.FAILED
            try:
                await self._cleanup_agent(worker.worker_id)
            except Exception:
                # Keep the owned Agent reference so delete() or a later submit
                # can retry cleanup. Do not mask the start/chat/result failure.
                pass
            raise
        return self._statuses[worker.worker_id]

    async def _cleanup_agent(self, worker_id: str) -> None:
        agent_id = self._agents[worker_id]
        state = self.lifecycle.inspect(agent_id).state
        if state in {AgentLifecycleState.READY, AgentLifecycleState.FAILED}:
            await self.lifecycle.stop(agent_id)
            state = self.lifecycle.inspect(agent_id).state
        if state is not AgentLifecycleState.STOPPED:
            raise RuntimeError("worker Agent is not in a deletable state")
        await self.lifecycle.delete(agent_id)
        self._agents.pop(worker_id, None)

    async def status(self, worker_id: str) -> WorkerStatus:
        return self._statuses[worker_id]

    async def watch(self, worker_id: str) -> AsyncIterator[WorkerStatus]:
        yield await self.status(worker_id)

    async def suspend(self, worker_id: str) -> WorkerStatus:
        raise NotImplementedError("legacy lifecycle has no suspend operation")

    async def resume(self, worker_id: str) -> WorkerStatus:
        raise NotImplementedError("legacy lifecycle has no resume operation")

    async def delete(self, worker_id: str) -> None:
        await self._cleanup_agent(worker_id)
        self._statuses.pop(worker_id, None)
        self.results.pop(worker_id, None)
