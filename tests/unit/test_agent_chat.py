"""Deterministic chat concurrency, lifecycle, and failure policy checks."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.runtime_support.fake_interaction import FakeInteraction
from tests.unit.test_agent_lifecycle import _ReadyRuntime, _service
from universal_agent_runtime.application.agent_chat import (
    AgentChatService,
    ChatConfiguration,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleFailure,
    CreateAgentCommand,
)
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode as Code,
)
from universal_agent_runtime.application.ports.interaction_values import (
    TurnRequest,
    TurnResult,
)
from universal_agent_runtime.domain.agent import AgentLifecycleState as State


class GatedInteraction(FakeInteraction):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def turn(self, request: TurnRequest) -> TurnResult:
        if request.session.agent_id.value == "agent-one":
            self.entered.set()
            await self.release.wait()
        return await super().turn(request)


def test_busy_rejects_overlap_and_lifecycle_but_other_agent_can_progress() -> None:
    async def scenario() -> None:
        interaction = GatedInteraction()
        lifecycle, repository = _service(_ReadyRuntime(), interaction, "one", "two")
        chat = AgentChatService(interaction, repository, ChatConfiguration())
        one = (await lifecycle.create(CreateAgentCommand("one"))).agent
        two = (await lifecycle.create(CreateAgentCommand("two"))).agent
        await lifecycle.start(one.agent_id)
        await lifecycle.start(two.agent_id)
        pending = asyncio.create_task(chat.send(one.agent_id, "private-one"))
        await interaction.entered.wait()
        assert lifecycle.inspect(one.agent_id).state is State.BUSY
        for operation in (
            chat.send(one.agent_id, "overlap"),
            lifecycle.stop(one.agent_id),
            lifecycle.start(one.agent_id),
            lifecycle.delete(one.agent_id),
        ):
            with pytest.raises(AgentLifecycleFailure) as failure:
                await operation
            assert failure.value.code.value == "invalid_state"
        assert chat.history(one.agent_id).messages == ()
        result = await chat.send(two.agent_id, "private-two")
        assert "private-one" not in result[-1].content
        # Cancellation only detaches the caller; it cannot unlock a running turn.
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert lifecycle.inspect(one.agent_id).state is State.BUSY
        interaction.release.set()
        await chat.close()
        assert lifecycle.inspect(one.agent_id).state is State.READY
        assert len(chat.history(one.agent_id).messages) == 2
        assert len(interaction.turns[one.session]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "code,recoverable",
    [
        (Code.INFERENCE_UNAVAILABLE, True),
        (Code.TIMEOUT, True),
        (Code.TOOL_FAILED, True),
        (Code.VALIDATION_FAILED, True),
        (Code.NOT_FOUND, False),
        (Code.CORRUPT_STATE, False),
        (Code.INCOMPATIBLE_STATE, False),
        (Code.PROTOCOL_FAILURE, False),
        (Code.OPERATION_FAILED, False),
    ],
)
def test_failures_preserve_history_and_runtime(code: Code, recoverable: bool) -> None:
    async def scenario() -> None:
        interaction = FakeInteraction()
        runtime = _ReadyRuntime()
        lifecycle, repository = _service(runtime, interaction, "one")
        chat = AgentChatService(interaction, repository, ChatConfiguration())
        record = (await lifecycle.create(CreateAgentCommand("one"))).agent
        await lifecycle.start(record.agent_id)
        await chat.send(record.agent_id, "retained")
        before = chat.history(record.agent_id)
        interaction.turn_failure = code
        with pytest.raises(AgentLifecycleFailure) as failure:
            await chat.send(record.agent_id, "failed")
        assert failure.value.retryable is recoverable
        observed = lifecycle.inspect(record.agent_id)
        assert observed.state is (State.READY if recoverable else State.FAILED)
        assert observed.runtime_handle == record.runtime_handle
        assert observed.failure is not None
        assert chat.history(record.agent_id) == before
        assert interaction.delete_calls == 0
        assert record.session in interaction.sessions
        if recoverable:
            interaction.turn_failure = None
            result = await chat.send(record.agent_id, "retry")
            assert result[-1].sequence == 4
            assert "retained" in result[-1].content
        else:
            await lifecycle.stop(record.agent_id)
            with pytest.raises(AgentLifecycleFailure):
                await lifecycle.start(record.agent_id)
            assert lifecycle.inspect(record.agent_id).conversation_recovery_required
            await lifecycle.delete(record.agent_id)

    asyncio.run(scenario())


def test_validation_retention_limit_and_stable_pagination() -> None:
    async def scenario() -> None:
        interaction = FakeInteraction()
        lifecycle, repository = _service(_ReadyRuntime(), interaction, "one")
        chat = AgentChatService(
            interaction,
            repository,
            ChatConfiguration(
                max_message_characters=8,
                max_history_messages=4,
                max_history_page_size=1,
            ),
        )
        record = (await lifecycle.create(CreateAgentCommand("one"))).agent
        with pytest.raises(AgentLifecycleFailure):
            await chat.send(record.agent_id, "stopped")
        await lifecycle.start(record.agent_id)
        for content in ("", " ", "\x00", "x" * 9):
            with pytest.raises(AgentLifecycleFailure):
                await chat.send(record.agent_id, content)
        first = await chat.send(record.agent_id, "one")
        await lifecycle.stop(record.agent_id)
        await lifecycle.start(record.agent_id)
        second = await chat.send(record.agent_id, "two")
        assert first[0].turn_id == first[1].turn_id != second[0].turn_id
        assert len({m.message_id for m in first + second}) == 4
        assert all(m.created_at.utcoffset() == timedelta(0) for m in first + second)
        assert first[0].created_at <= first[1].created_at <= second[0].created_at
        page = chat.history(record.agent_id)
        assert page.messages == first[:1] and page.next_after == 1
        assert chat.history(record.agent_id, after=3).messages == second[1:]
        assert chat.history(record.agent_id, after=4).messages == ()
        for after, limit in ((-1, 1), (0, 2), (0, 0)):
            with pytest.raises(AgentLifecycleFailure):
                chat.history(record.agent_id, after=after, limit=limit)
        with pytest.raises(AgentLifecycleFailure) as failure:
            await chat.send(record.agent_id, "full")
        assert failure.value.code.value == "history_limit"
        assert lifecycle.inspect(record.agent_id).state is State.READY
        await lifecycle.stop(record.agent_id)
        await lifecycle.delete(record.agent_id)
        with pytest.raises(AgentLifecycleFailure):
            chat.history(record.agent_id)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mode", ["unexpected", "wrong_session", "wrong_count", "oversized"]
)
def test_indeterminate_adapter_outcomes_never_commit_or_leave_busy(mode: str) -> None:
    class BadInteraction(FakeInteraction):
        async def turn(self, request: TurnRequest) -> TurnResult:
            if mode == "unexpected":
                raise RuntimeError("private endpoint and credential")
            result = await super().turn(request)
            if mode == "wrong_session":
                from universal_agent_runtime.domain.identifiers import SessionId

                return replace(
                    result,
                    session=replace(result.session, session_id=SessionId("foreign")),
                )
            if mode == "wrong_count":
                return replace(result, completed_turns=7)
            return replace(result, response="x" * 30)

    async def scenario() -> None:
        interaction = BadInteraction()
        lifecycle, repository = _service(_ReadyRuntime(), interaction, "one")
        chat = AgentChatService(
            interaction, repository, ChatConfiguration(max_response_characters=20)
        )
        record = (await lifecycle.create(CreateAgentCommand("one"))).agent
        await lifecycle.start(record.agent_id)
        with pytest.raises(AgentLifecycleFailure):
            await chat.send(record.agent_id, "hello")
        assert lifecycle.inspect(record.agent_id).state is State.FAILED
        assert chat.history(record.agent_id).messages == ()

    asyncio.run(scenario())
