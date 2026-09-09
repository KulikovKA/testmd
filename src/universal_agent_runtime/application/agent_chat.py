"""Bounded non-streaming turns with process-local, per-Agent coordination."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import uuid4

from universal_agent_runtime.application.agent_lifecycle import (
    AgentFailure,
    AgentLifecycleFailure,
    AgentRecord,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleErrorCode as Code,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleOperation as Op,
)
from universal_agent_runtime.application.ports.agent_interaction import AgentInteraction
from universal_agent_runtime.application.ports.agent_repository import AgentRepository
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
    InteractionFailure,
)
from universal_agent_runtime.application.ports.interaction_values import TurnRequest
from universal_agent_runtime.domain.agent import AgentLifecycleState as State
from universal_agent_runtime.domain.identifiers import AgentId
from universal_agent_runtime.domain.message import Message


@dataclass(frozen=True)
class ChatConfiguration:
    max_message_characters: int = 16_384
    max_response_characters: int = 16_384
    max_history_messages: int = 100
    max_history_page_size: int = 50
    redacted_values: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        for value in (
            self.max_message_characters,
            self.max_response_characters,
            self.max_history_messages,
            self.max_history_page_size,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("chat limits must be positive integers")
        if self.max_message_characters > 16_384 or self.max_history_messages < 2:
            raise ValueError("invalid chat limits")


@dataclass(frozen=True)
class MessagePage:
    messages: tuple[Message, ...]
    next_after: int | None


@dataclass(frozen=True)
class AcceptedTurn:
    """One owned turn shared by JSON and event-stream transports."""

    agent_id: AgentId
    turn_id: str
    task: asyncio.Task[tuple[Message, ...]] = field(repr=False)


class AgentChatService:
    """Single event-loop owner: reserve BUSY before the first await; reject overlap."""

    def __init__(
        self,
        interaction: AgentInteraction,
        repository: AgentRepository,
        configuration: ChatConfiguration,
    ) -> None:
        self._interaction = interaction
        self._repository = repository
        self._configuration = configuration
        self._pending: set[asyncio.Task[tuple[Message, ...]]] = set()

    def _require(self, agent_id: AgentId, operation: Op) -> AgentRecord:
        record = self._repository.get(agent_id)
        if record is None:
            raise AgentLifecycleFailure(operation, Code.NOT_FOUND, agent_id=agent_id)
        return record

    def _redact(self, content: str) -> str:
        for value in sorted(self._configuration.redacted_values, key=len, reverse=True):
            if value:
                content = content.replace(value, "[REDACTED]")
        return content

    def history(
        self, agent_id: AgentId, *, after: int = 0, limit: int | None = None
    ) -> MessagePage:
        record = self._require(agent_id, Op.HISTORY)
        maximum = self._configuration.max_history_page_size
        limit = maximum if limit is None else limit
        if (
            type(after) is not int
            or after < 0
            or type(limit) is not int
            or not 1 <= limit <= maximum
        ):
            raise AgentLifecycleFailure(
                Op.HISTORY, Code.MESSAGE_INVALID, agent_id=agent_id
            )
        messages = tuple(m for m in record.messages if m.sequence > after)
        page = messages[:limit]
        return MessagePage(page, page[-1].sequence if len(messages) > limit else None)

    async def send(self, agent_id: AgentId, content: str) -> tuple[Message, ...]:
        turn = self.begin(agent_id, content)
        return await asyncio.shield(turn.task)

    def begin(self, agent_id: AgentId, content: str) -> AcceptedTurn:
        """Validate and reserve before HTTP headers; never await or queue a turn."""
        record = self._require(agent_id, Op.MESSAGE)
        if record.state is not State.READY or record.conversation_recovery_required:
            raise AgentLifecycleFailure(
                Op.MESSAGE, Code.INVALID_STATE, agent_id=agent_id, state=record.state
            )
        try:
            request = TurnRequest(record.session, content)
            if len(content) > self._configuration.max_message_characters:
                raise ValueError
            request = TurnRequest(record.session, self._redact(content))
        except (TypeError, ValueError):
            raise AgentLifecycleFailure(
                Op.MESSAGE, Code.MESSAGE_INVALID, agent_id=agent_id, state=record.state
            ) from None
        if len(record.messages) + 2 > self._configuration.max_history_messages:
            raise AgentLifecycleFailure(
                Op.MESSAGE, Code.HISTORY_LIMIT, agent_id=agent_id, state=record.state
            )
        busy = replace(record, state=State.BUSY, failure=None)
        self._repository.save(busy)
        turn_id = uuid4().hex
        task = asyncio.create_task(self._complete(busy, request, turn_id))
        self._pending.add(task)
        task.add_done_callback(self._finished)
        # Disconnect/caller cancellation must not release BUSY while an adapter
        # thread still writes native state. The owned task commits its outcome.
        return AcceptedTurn(agent_id, turn_id, task)

    def _finished(self, task: asyncio.Task[tuple[Message, ...]]) -> None:
        self._pending.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve failures even if the HTTP caller disconnected.

    def _fail(
        self,
        record: AgentRecord,
        code: Code,
        *,
        recoverable: bool,
        interaction_code: InteractionErrorCode | None = None,
    ) -> AgentLifecycleFailure:
        state = State.READY if recoverable else State.FAILED
        self._repository.save(
            replace(
                record,
                state=state,
                failure=AgentFailure(Op.MESSAGE, code, recoverable, interaction_code),
                conversation_recovery_required=not recoverable,
            )
        )
        return AgentLifecycleFailure(
            Op.MESSAGE,
            code,
            agent_id=record.agent_id,
            state=state,
            retryable=recoverable,
            interaction_code=interaction_code,
        )

    async def _complete(
        self, record: AgentRecord, request: TurnRequest, turn_id: str
    ) -> tuple[Message, ...]:
        started = datetime.now(UTC)
        try:
            result = await self._interaction.turn(request)
            if (
                result.session != record.session
                or result.completed_turns != len(record.messages) // 2 + 1
                or len(result.response) > self._configuration.max_response_characters
                or not result.response.strip()
                or "\x00" in result.response
            ):
                raise self._fail(record, Code.INTERACTION_FAILED, recoverable=False)
            messages = (
                Message(
                    uuid4().hex,
                    turn_id,
                    len(record.messages) + 1,
                    "user",
                    request.message,
                    started,
                ),
                Message(
                    uuid4().hex,
                    turn_id,
                    len(record.messages) + 2,
                    "assistant",
                    self._redact(result.response),
                    datetime.now(UTC),
                ),
            )
            self._repository.save(
                replace(
                    record,
                    state=State.READY,
                    messages=record.messages + messages,
                    failure=None,
                )
            )
            return messages
        except InteractionFailure as failure:
            recoverable = {
                InteractionErrorCode.INFERENCE_UNAVAILABLE: Code.INFERENCE_UNAVAILABLE,
                InteractionErrorCode.TIMEOUT: Code.TIMEOUT,
                InteractionErrorCode.VALIDATION_FAILED: Code.HISTORY_LIMIT,
                InteractionErrorCode.TOOL_FAILED: Code.TOOL_FAILED,
            }
            raise self._fail(
                record,
                recoverable.get(failure.code, Code.INTERACTION_FAILED),
                recoverable=failure.code in recoverable,
                interaction_code=failure.code,
            ) from None
        except AgentLifecycleFailure:
            raise
        except asyncio.CancelledError:
            self._fail(record, Code.INTERACTION_FAILED, recoverable=False)
            raise
        except Exception:  # noqa: BLE001 - boundary maps unknown outcomes to FAILED
            # Unknown adapter outcomes are indeterminate; do not retry a turn.
            raise self._fail(
                record, Code.INTERACTION_FAILED, recoverable=False
            ) from None

    async def close(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
