"""Turn-bound SSE transport: lifecycle events and committed, buffered content."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse
from starlette.types import Message as ASGIMessage
from starlette.types import Send

from universal_agent_runtime.application.agent_chat import AcceptedTurn
from universal_agent_runtime.application.agent_lifecycle import AgentLifecycleFailure


class StartedData(BaseModel):
    type: Literal["started"] = "started"
    content_mode: Literal["committed_response"] = "committed_response"


class ContentData(BaseModel):
    type: Literal["content"] = "content"
    content: str = Field(repr=False)
    message_id: str


class ToolData(BaseModel):
    """Reserved vocabulary; tools are disabled and this version never emits it."""

    type: Literal["tool"] = "tool"
    tool_name: str
    phase: Literal["started", "completed", "failed"]


class CompletedData(BaseModel):
    type: Literal["completed"] = "completed"
    message_ids: tuple[str, str]


class ErrorData(BaseModel):
    type: Literal["error"] = "error"
    code: str
    state: str | None = None
    retryable: bool = False
    interaction_code: str | None = None


class StreamEvent(BaseModel):
    """Versioned transport-only envelope. Sequence is scoped to this turn."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    agent_id: str
    turn_id: str
    sequence: int = Field(ge=1)
    data: Annotated[
        StartedData | ContentData | ToolData | CompletedData | ErrorData,
        Field(discriminator="type"),
    ]

    def encode(self) -> bytes:
        # JSON escaping keeps newlines and model text out of SSE control fields.
        return (
            f"id: {self.turn_id}:{self.sequence}\n"
            f"event: {self.data.type}\n"
            f"data: {self.model_dump_json(exclude_none=True)}\n\n"
        ).encode()


async def turn_events(
    turn: AcceptedTurn, heartbeat_seconds: float
) -> AsyncGenerator[bytes, None]:
    def event(
        sequence: int, data: StartedData | ContentData | CompletedData | ErrorData
    ) -> bytes:
        return StreamEvent(
            agent_id=turn.agent_id.value,
            turn_id=turn.turn_id,
            sequence=sequence,
            data=data,
        ).encode()

    yield event(1, StartedData())
    while not turn.task.done():
        done, _ = await asyncio.wait({turn.task}, timeout=heartbeat_seconds)
        if not done:
            yield b": keep-alive\n\n"
    try:
        messages = turn.task.result()
    except AgentLifecycleFailure as failure:
        yield event(
            2,
            ErrorData(
                code=failure.code.value,
                state=failure.state.value if failure.state else None,
                retryable=failure.retryable,
                interaction_code=failure.interaction_code.value
                if failure.interaction_code
                else None,
            ),
        )
        return
    except asyncio.CancelledError:
        yield event(2, ErrorData(code="interaction_failed", state="FAILED"))
        return
    yield event(
        2, ContentData(content=messages[1].content, message_id=messages[1].message_id)
    )
    yield event(
        3, CompletedData(message_ids=(messages[0].message_id, messages[1].message_id))
    )


class TurnEventResponse(StreamingResponse):
    """Bound each network send; no event queue or independent producer exists."""

    def __init__(
        self,
        turn: AcceptedTurn,
        *,
        heartbeat_seconds: float,
        send_timeout_seconds: float,
    ) -> None:
        self._events = turn_events(turn, heartbeat_seconds)
        self._send_timeout_seconds = send_timeout_seconds
        super().__init__(
            self._events,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Turn-ID": turn.turn_id,
            },
        )

    async def stream_response(self, send: Send) -> None:
        async def bounded_send(message: ASGIMessage) -> None:
            try:
                async with asyncio.timeout(self._send_timeout_seconds):
                    await send(message)
            except TimeoutError:
                raise OSError("event stream send timed out") from None

        try:
            await super().stream_response(bounded_send)
        finally:
            # Disconnect never cancels the application-owned task or releases BUSY.
            await self._events.aclose()
