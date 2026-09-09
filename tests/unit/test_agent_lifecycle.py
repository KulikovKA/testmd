"""Unit coverage for Agent lifecycle policy and recovery semantics."""

import asyncio
from collections.abc import Iterator

import pytest

from tests.runtime_support.fake_interaction import FakeInteraction
from tests.runtime_support.fake_runtime import FakeRuntime
from tests.runtime_support.harness import Phase
from universal_agent_runtime.adapters.in_memory_agent_repository import (
    InMemoryAgentRepository,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleErrorCode,
    AgentLifecycleFailure,
    AgentLifecycleService,
    CreateAgentCommand,
    LifecycleConfiguration,
)
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
)
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeOperation,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    OperationOptions,
    ResourceLimits,
    RuntimeHandle,
    RuntimeObservation,
)
from universal_agent_runtime.domain.agent import AgentLifecycleState


class _ReadyRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.create_calls = 0
        self.start_calls = 0

    async def create(
        self, request: CreateRuntimeRequest, *, options: OperationOptions
    ) -> RuntimeObservation:
        self.create_calls += 1
        return await super().create(request, options=options)

    async def start(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        self.start_calls += 1
        await super().start(handle, options=options)
        await self.confirm_ready(handle)
        return await self.status(handle, options=options)


def _tokens(*values: str) -> Iterator[str]:
    yield from values


def _service(
    runtime: FakeRuntime,
    interaction: FakeInteraction,
    *tokens: str,
    readiness_timeout: float = 0.05,
) -> tuple[AgentLifecycleService, InMemoryAgentRepository]:
    repository = InMemoryAgentRepository()
    sequence = _tokens(*tokens)
    return (
        AgentLifecycleService(
            runtime,
            interaction,
            repository,
            LifecycleConfiguration(
                workload="test-workload",
                resources=ResourceLimits(1.0, 64 * 1024 * 1024),
                environment=(),
                secrets=(),
                network=(),
                operation_options=OperationOptions(1),
                readiness_timeout_seconds=readiness_timeout,
                readiness_poll_interval_seconds=0.001,
            ),
            identifier_factory=lambda: next(sequence),
        ),
        repository,
    )


def test_create_is_idempotent_and_snapshots_unique_agent_boundaries() -> None:
    runtime = _ReadyRuntime()
    interaction = FakeInteraction()
    service, _ = _service(runtime, interaction, "one", "two")

    async def scenario() -> None:
        command = CreateAgentCommand("request-one", ("skill-a",), ("tool-a",))
        first, replay = await asyncio.gather(
            service.create(command), service.create(command)
        )
        assert {first.created, replay.created} == {True, False}
        assert first.agent.agent_id == replay.agent.agent_id
        assert first.agent.state is AgentLifecycleState.STOPPED
        assert first.agent.agent_id.value == "agent-one"
        assert first.agent.workspace_id.value == "workspace-one"
        assert first.agent.session.session_id.value == "session-one"
        assert first.agent.configuration.skills == ("skill-a",)
        assert first.agent.configuration.tools == ("tool-a",)
        assert runtime.create_calls == 1
        assert interaction.create_calls == 1

        with pytest.raises(AgentLifecycleFailure) as mismatch:
            await service.create(
                CreateAgentCommand("request-one", ("different-skill",), ())
            )
        assert mismatch.value.code is AgentLifecycleErrorCode.CONFLICT

        second = await service.create(CreateAgentCommand("request-two"))
        assert second.agent.agent_id != first.agent.agent_id
        assert second.agent.workspace_id != first.agent.workspace_id
        assert second.agent.session != first.agent.session

    asyncio.run(scenario())


def test_start_stop_delete_are_idempotent_and_preserve_session_until_delete() -> None:
    runtime = _ReadyRuntime()
    interaction = FakeInteraction()
    service, _ = _service(runtime, interaction, "one")

    async def scenario() -> None:
        created = (await service.create(CreateAgentCommand("request-one"))).agent
        started, replay = await asyncio.gather(
            service.start(created.agent_id), service.start(created.agent_id)
        )
        assert started.state is AgentLifecycleState.READY
        assert replay.state is AgentLifecycleState.READY
        assert runtime.start_calls == 1
        assert started.runtime_handle is not None
        await runtime.write_workspace(started.runtime_handle, "preserved")

        stopped = await service.stop(created.agent_id)
        assert stopped.state is AgentLifecycleState.STOPPED
        assert created.session in interaction.sessions
        assert await runtime.read_workspace(started.runtime_handle) == "preserved"
        assert (
            await service.stop(created.agent_id)
        ).state is AgentLifecycleState.STOPPED

        restarted = await service.start(created.agent_id)
        assert restarted.state is AgentLifecycleState.READY
        assert await runtime.read_workspace(started.runtime_handle) == "preserved"
        await service.stop(created.agent_id)

        deleted = await service.delete(created.agent_id)
        assert deleted.already_deleted is False
        assert created.session not in interaction.sessions
        assert (await service.delete(created.agent_id)).already_deleted is True
        with pytest.raises(AgentLifecycleFailure) as missing:
            service.inspect(created.agent_id)
        assert missing.value.code is AgentLifecycleErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_starting_state_is_observable_while_conflicting_operation_is_serialized() -> (
    None
):
    runtime = _ReadyRuntime()
    interaction = FakeInteraction()
    service, _ = _service(runtime, interaction, "one")

    async def scenario() -> None:
        created = (await service.create(CreateAgentCommand("request-one"))).agent
        pause = runtime.pause_next(RuntimeOperation.START, Phase.BEFORE)
        start = asyncio.create_task(service.start(created.agent_id))
        await pause.entered.wait()
        assert service.inspect(created.agent_id).state is AgentLifecycleState.STARTING
        stop = asyncio.create_task(service.stop(created.agent_id))
        assert not stop.done()
        pause.release.set()
        assert (await start).state is AgentLifecycleState.READY
        assert (await stop).state is AgentLifecycleState.STOPPED

    asyncio.run(scenario())


def test_readiness_timeout_is_failed_but_recoverable_through_stop_and_delete() -> None:
    runtime = FakeRuntime()
    interaction = FakeInteraction()
    service, _ = _service(runtime, interaction, "one", readiness_timeout=0.01)

    async def scenario() -> None:
        created = (await service.create(CreateAgentCommand("request-one"))).agent
        with pytest.raises(AgentLifecycleFailure) as timeout:
            await service.start(created.agent_id)
        assert timeout.value.code is AgentLifecycleErrorCode.READINESS_TIMEOUT
        failed = service.inspect(created.agent_id)
        assert failed.state is AgentLifecycleState.FAILED
        assert failed.failure is not None
        assert failed.failure.retryable is True
        assert (
            await service.stop(created.agent_id)
        ).state is AgentLifecycleState.STOPPED
        await service.delete(created.agent_id)

    asyncio.run(scenario())


def test_runtime_cleanup_failure_retains_evidence_and_retries_same_identity() -> None:
    runtime = _ReadyRuntime()
    interaction = FakeInteraction()
    service, _ = _service(runtime, interaction, "one")

    async def scenario() -> None:
        created = (await service.create(CreateAgentCommand("request-one"))).agent
        runtime.fail_cleanup_once()
        with pytest.raises(AgentLifecycleFailure) as cleanup:
            await service.delete(created.agent_id)
        assert cleanup.value.code is AgentLifecycleErrorCode.CLEANUP_FAILED
        retained = service.inspect(created.agent_id)
        assert retained.state is AgentLifecycleState.FAILED
        assert retained.runtime_handle == created.runtime_handle
        assert created.session in interaction.sessions
        await service.delete(created.agent_id)
        assert created.session not in interaction.sessions

    asyncio.run(scenario())


def test_session_cleanup_failure_retains_record_after_runtime_delete() -> None:
    runtime = _ReadyRuntime()
    interaction = FakeInteraction()
    service, _ = _service(runtime, interaction, "one")

    async def scenario() -> None:
        created = (await service.create(CreateAgentCommand("request-one"))).agent
        interaction.fail_delete_once(InteractionErrorCode.CLEANUP_FAILED)
        with pytest.raises(AgentLifecycleFailure) as cleanup:
            await service.delete(created.agent_id)
        assert cleanup.value.code is AgentLifecycleErrorCode.CLEANUP_FAILED
        assert service.inspect(created.agent_id).state is AgentLifecycleState.FAILED
        await service.delete(created.agent_id)
        assert interaction.delete_calls == 2

    asyncio.run(scenario())


def test_session_create_failure_retains_runtime_for_explicit_cleanup() -> None:
    runtime = _ReadyRuntime()
    interaction = FakeInteraction()
    interaction.fail_create_once(InteractionErrorCode.OPERATION_FAILED)
    service, _ = _service(runtime, interaction, "one")

    async def scenario() -> None:
        with pytest.raises(AgentLifecycleFailure) as session_failure:
            await service.create(CreateAgentCommand("request-one"))
        assert session_failure.value.code is AgentLifecycleErrorCode.SESSION_FAILED
        agent_id = session_failure.value.agent_id
        assert agent_id is not None
        retained = service.inspect(agent_id)
        assert retained.state is AgentLifecycleState.FAILED
        assert retained.runtime_handle is not None
        await service.delete(agent_id)

    asyncio.run(scenario())


def test_delete_rejects_ready_agent_without_losing_record() -> None:
    runtime = _ReadyRuntime()
    interaction = FakeInteraction()
    service, _ = _service(runtime, interaction, "one")

    async def scenario() -> None:
        created = (await service.create(CreateAgentCommand("request-one"))).agent
        await service.start(created.agent_id)
        with pytest.raises(AgentLifecycleFailure) as invalid:
            await service.delete(created.agent_id)
        assert invalid.value.code is AgentLifecycleErrorCode.INVALID_STATE
        assert service.inspect(created.agent_id).state is AgentLifecycleState.READY

    asyncio.run(scenario())
