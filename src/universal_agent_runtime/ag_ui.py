"""Minimal AG-UI SSE transport over the existing committed-turn chat service."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.responses import StreamingResponse

from universal_agent_runtime.application.agent_chat import AcceptedTurn, AgentChatService
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleErrorCode,
    AgentLifecycleFailure,
)
from universal_agent_runtime.domain.identifiers import AgentId


class RunAgentInput(BaseModel):
    """Supported, transport-only AG-UI RunAgentInput subset.

    The orchestrator owns conversation history and configured tools.  The caller's
    last plain-text user message is the one new turn accepted by this endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    threadId: str = Field(min_length=1, max_length=128)
    runId: str = Field(min_length=1, max_length=128)
    state: Any
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    context: list[dict[str, Any]]
    forwardedProps: Any
    parentRunId: str | None = Field(default=None, min_length=1, max_length=128)
    resume: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def supported_turn(self) -> "RunAgentInput":
        if not self.messages:
            raise ValueError("a current user message is required")
        current = self.messages[-1]
        if (
            current.get("role") != "user"
            or not isinstance(current.get("content"), str)
            or not current["content"].strip()
            or "\x00" in current["content"]
        ):
            raise ValueError("the last message must be non-empty user text")
        return self

    @property
    def current_message(self) -> str:
        value = self.messages[-1]["content"]
        assert isinstance(value, str)
        return value


def _event(payload: dict[str, Any]) -> bytes:
    # The AG-UI EventEncoder SSE form is one JSON event in a data field.
    return ("data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode()


def safe_failure(failure: AgentLifecycleFailure) -> tuple[str, str]:
    messages = {
        AgentLifecycleErrorCode.NOT_FOUND: "Agent not found",
        AgentLifecycleErrorCode.INVALID_STATE: "Agent is unavailable for this run",
        AgentLifecycleErrorCode.CONFLICT: "Agent run conflicts with existing work",
        AgentLifecycleErrorCode.MESSAGE_INVALID: "Message was rejected",
        AgentLifecycleErrorCode.HISTORY_LIMIT: "Conversation limit reached",
        AgentLifecycleErrorCode.INFERENCE_UNAVAILABLE: "Agent inference is unavailable",
        AgentLifecycleErrorCode.TOOL_FAILED: "Agent tool operation failed",
        AgentLifecycleErrorCode.INTERACTION_FAILED: "Agent conversation requires recovery",
    }
    return failure.code.value, messages.get(failure.code, "Agent run failed")


async def ag_ui_events(
    turn: AcceptedTurn,
    *,
    thread_id: str,
    run_id: str,
    heartbeat_seconds: float,
) -> AsyncGenerator[bytes, None]:
    """Emit a standard AG-UI lifecycle around the single application-owned turn."""

    yield _event({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})
    while not turn.task.done():
        done, _ = await asyncio.wait({turn.task}, timeout=heartbeat_seconds)
        if not done:
            yield b": keep-alive\n\n"
    try:
        messages = turn.task.result()
    except AgentLifecycleFailure as failure:
        code, message = safe_failure(failure)
        yield _event({"type": "RUN_ERROR", "message": message, "code": code})
        return
    except asyncio.CancelledError:
        yield _event(
            {
                "type": "RUN_ERROR",
                "message": "Agent run was cancelled",
                "code": "interaction_failed",
            }
        )
        return
    assistant = messages[1]
    yield _event(
        {
            "type": "TEXT_MESSAGE_START",
            "messageId": assistant.message_id,
            "role": "assistant",
        }
    )
    yield _event(
        {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": assistant.message_id,
            "delta": assistant.content,
        }
    )
    yield _event({"type": "TEXT_MESSAGE_END", "messageId": assistant.message_id})
    yield _event({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})


async def ag_ui_error_events(
    *, thread_id: str | None, run_id: str | None, code: str, message: str
) -> AsyncGenerator[bytes, None]:
    if thread_id is not None and run_id is not None:
        yield _event({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})
    yield _event({"type": "RUN_ERROR", "message": message, "code": code})


class AGUIEventResponse(StreamingResponse):
    """SSE response using the official AG-UI event framing."""

    def __init__(self, content: AsyncGenerator[bytes, None]) -> None:
        super().__init__(
            content,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )


def parse_run_input(raw: object) -> tuple[RunAgentInput | None, str | None, str | None]:
    """Keep malformed input in the AG-UI error stream without echoing payloads."""

    thread_id = None
    run_id = None
    if isinstance(raw, dict):
        thread = raw.get("threadId")
        run = raw.get("runId")
        if isinstance(thread, str) and 0 < len(thread) <= 128:
            thread_id = thread
        if isinstance(run, str) and 0 < len(run) <= 128:
            run_id = run
    try:
        return RunAgentInput.model_validate(raw), thread_id, run_id
    except ValidationError:
        return None, thread_id, run_id


def begin_run(chat: AgentChatService, agent_id: str, input: RunAgentInput) -> AcceptedTurn:
    """Keep Agent identity and lifecycle checks in the existing application service."""

    return chat.begin(AgentId(agent_id), input.current_message)
