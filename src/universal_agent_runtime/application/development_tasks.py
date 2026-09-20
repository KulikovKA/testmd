"""Task admission and ownership using the existing Agent lifecycle records."""

from dataclasses import replace
from uuid import uuid4

from universal_agent_runtime.application.agent_lifecycle import AgentRecord
from universal_agent_runtime.application.ports.agent_repository import AgentRepository
from universal_agent_runtime.application.ports.development_tasks import (
    DevelopmentTaskRepository,
)
from universal_agent_runtime.domain.agent import AgentLifecycleState
from universal_agent_runtime.domain.development_task import (
    TERMINAL_STATES,
    DevelopmentFailure,
    DevelopmentRequest,
    DevelopmentState,
    DevelopmentTask,
)
from universal_agent_runtime.domain.identifiers import validate_identifier


class DevelopmentTaskService:
    def __init__(
        self, tasks: DevelopmentTaskRepository, agents: AgentRepository
    ) -> None:
        self.tasks = tasks
        self.agents = agents

    def create(self, request: DevelopmentRequest) -> DevelopmentTask:
        agent = self.agents.get(request.agent_id)
        if (
            agent is None
            or agent.state is not AgentLifecycleState.READY
            or agent.conversation_recovery_required
        ):
            raise DevelopmentFailure("agent_unavailable")
        task = DevelopmentTask(uuid4().hex, request)
        self.tasks.add(task)
        return task

    def get(self, task_id: str) -> DevelopmentTask:
        validate_identifier(task_id)
        task = self.tasks.get(task_id)
        if task is None:
            raise DevelopmentFailure("task_not_found")
        return task

    def claim(self, task_id: str) -> AgentRecord:
        task = self.get(task_id)
        if task.state not in {
            DevelopmentState.CREATED,
            DevelopmentState.WAITING_FOR_CLARIFICATION,
        }:
            raise DevelopmentFailure("task_conflict")
        if (
            task.state is DevelopmentState.WAITING_FOR_CLARIFICATION
            and not task.clarification
        ):
            raise DevelopmentFailure("clarification_required")
        agent = self.agents.get(task.request.agent_id)
        if (
            agent is None
            or agent.state is not AgentLifecycleState.READY
            or agent.conversation_recovery_required
        ):
            raise DevelopmentFailure("agent_unavailable")
        if agent.development_task_id is not None:
            raise DevelopmentFailure("task_conflict")
        owned = replace(agent, development_task_id=task_id)
        self.agents.save(owned)
        return owned

    def release(self, task_id: str) -> None:
        task = self.get(task_id)
        agent = self.agents.get(task.request.agent_id)
        if agent is not None and agent.development_task_id == task_id:
            self.agents.save(replace(agent, development_task_id=None))

    def transition(
        self, task_id: str, state: DevelopmentState, **changes: object
    ) -> DevelopmentTask:
        current = self.get(task_id)
        task = current.transition(state, **changes)
        self.tasks.save(task, expected_version=current.version)
        return task

    def clarify(self, task_id: str, answer: str) -> DevelopmentTask:
        task = self.get(task_id)
        agent = self.agents.get(task.request.agent_id)
        if task.state is not DevelopmentState.WAITING_FOR_CLARIFICATION or (
            agent and agent.development_task_id
        ):
            raise DevelopmentFailure("task_conflict")
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or len(answer) > 4000
            or "\x00" in answer
        ):
            raise DevelopmentFailure("invalid_request")
        updated = replace(task, clarification=answer, version=task.version + 1)
        self.tasks.save(updated, expected_version=task.version)
        return updated

    def cancel(self, task_id: str) -> DevelopmentTask:
        task = self.get(task_id)
        if task.state in TERMINAL_STATES:
            return task
        agent = self.agents.get(task.request.agent_id)
        if agent is not None and agent.development_task_id == task_id:
            # The owner finishes an in-flight operation before acknowledging cancel.
            updated = replace(task, cancel_requested=True, version=task.version + 1)
            self.tasks.save(updated, expected_version=task.version)
            return updated
        return self.transition(task_id, DevelopmentState.CANCELLED)
