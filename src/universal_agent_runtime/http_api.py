"""FastAPI boundary for the Agent Orchestrator lifecycle API."""

import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from universal_agent_runtime.ag_ui import (
    AGUIEventResponse,
    ag_ui_error_events,
    ag_ui_events,
    begin_run,
    parse_run_input,
    safe_failure,
)
from universal_agent_runtime.application.agent_lifecycle import (
    AgentLifecycleErrorCode,
    AgentLifecycleFailure,
    AgentLifecycleOperation,
    AgentRecord,
    CreateAgentCommand,
)
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
)
from universal_agent_runtime.composition import (
    ApplicationComposition,
    compose_application,
)
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.domain.agent import AgentLifecycleState
from universal_agent_runtime.domain.identifiers import AgentId
from universal_agent_runtime.domain.message import Message
from universal_agent_runtime.http_streaming import StreamEvent, TurnEventResponse


class HealthResponse(BaseModel):
    """Transport-only liveness representation."""

    status: Literal["ok"]


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16_384)

    @field_validator("content")
    @classmethod
    def safe_text(cls, content: str) -> str:
        if not content.strip() or "\x00" in content:
            raise ValueError("invalid message")
        return content


class MessageResponse(BaseModel):
    message_id: str
    turn_id: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime

    @classmethod
    def from_message(cls, message: Message) -> "MessageResponse":
        return cls(
            message_id=message.message_id,
            turn_id=message.turn_id,
            sequence=message.sequence,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )


class MessagesResponse(BaseModel):
    agent_id: str
    messages: list[MessageResponse]
    next_after: int | None = None


class ReadinessResponse(BaseModel):
    """Transport-only application-readiness representation."""

    status: Literal["ready"]
    runtime_driver: str


class ErrorDetail(BaseModel):
    """Stable, redacted error detail safe for transport responses."""

    code: str = Field(pattern=r"^[a-z_]+$")
    message: str
    operation: AgentLifecycleOperation | None = None
    agent_id: str | None = None
    state: AgentLifecycleState | None = None
    retryable: bool | None = None
    interaction_code: InteractionErrorCode | None = None


class ErrorResponse(BaseModel):
    """Common transport error envelope; never includes exception details."""

    error: ErrorDetail


class CreateAgentRequest(BaseModel):
    """Transport-only create payload with an explicit idempotency identity."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    skills: list[str] = Field(default_factory=list, max_length=32)
    tools: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("skills", "tools")
    @classmethod
    def validate_capabilities(cls, values: list[str]) -> list[str]:
        pattern = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
        if len(values) != len(set(values)):
            raise ValueError("capability IDs must not contain duplicates")
        if any(
            not isinstance(value, str) or re.fullmatch(pattern, value) is None
            for value in values
        ):
            raise ValueError("capability IDs must be valid identifiers")
        return values


class RuntimeObservationResponse(BaseModel):
    execution: str
    readiness: str


class AgentConfigurationResponse(BaseModel):
    workload: str
    cpu_cores: float
    memory_bytes: int
    skills: list[str]
    tools: list[str]


class AgentFailureResponse(BaseModel):
    operation: AgentLifecycleOperation
    code: AgentLifecycleErrorCode
    retryable: bool
    interaction_code: InteractionErrorCode | None = None


class AgentResponse(BaseModel):
    agent_id: str
    workspace_id: str
    session_id: str
    state: AgentLifecycleState
    configuration: AgentConfigurationResponse
    runtime: RuntimeObservationResponse | None
    failure: AgentFailureResponse | None
    conversation_recovery_required: bool

    @classmethod
    def from_record(cls, record: AgentRecord) -> "AgentResponse":
        observation = record.runtime_observation
        return cls(
            agent_id=record.agent_id.value,
            workspace_id=record.workspace_id.value,
            session_id=record.session.session_id.value,
            state=record.state,
            conversation_recovery_required=record.conversation_recovery_required,
            configuration=AgentConfigurationResponse(
                workload=record.configuration.workload,
                cpu_cores=record.configuration.resources.cpu_cores,
                memory_bytes=record.configuration.resources.memory_bytes,
                skills=list(record.configuration.skills),
                tools=list(record.configuration.tools),
            ),
            runtime=(
                RuntimeObservationResponse(
                    execution=observation.execution.value,
                    readiness=observation.readiness.value,
                )
                if observation is not None
                else None
            ),
            failure=(
                AgentFailureResponse(
                    operation=record.failure.operation,
                    code=record.failure.code,
                    retryable=record.failure.retryable,
                    interaction_code=record.failure.interaction_code,
                )
                if record.failure is not None
                else None
            ),
        )


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    operation: AgentLifecycleOperation | None = None,
    agent_id: str | None = None,
    state: AgentLifecycleState | None = None,
    retryable: bool | None = None,
    interaction_code: InteractionErrorCode | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            operation=operation,
            agent_id=agent_id,
            state=state,
            retryable=retryable,
            interaction_code=interaction_code,
        )
    )
    return JSONResponse(
        status_code=status_code, content=payload.model_dump(exclude_none=True)
    )


async def _http_exception_handler(_: Request, exception: Exception) -> JSONResponse:
    assert isinstance(exception, StarletteHTTPException)
    if exception.status_code == 404:
        return _error(404, "not_found", "Resource not found")
    return _error(exception.status_code, "request_rejected", "Request was rejected")


async def _validation_exception_handler(
    _: Request, exception: Exception
) -> JSONResponse:
    assert isinstance(exception, RequestValidationError)
    return _error(422, "request_invalid", "Request validation failed")


async def _unhandled_exception_handler(_: Request, __: Exception) -> JSONResponse:
    return _error(500, "internal_error", "Internal server error")


async def _lifecycle_exception_handler(
    _: Request, exception: Exception
) -> JSONResponse:
    assert isinstance(exception, AgentLifecycleFailure)
    statuses = {
        AgentLifecycleErrorCode.NOT_FOUND: 404,
        AgentLifecycleErrorCode.INVALID_STATE: 409,
        AgentLifecycleErrorCode.CONFLICT: 409,
        AgentLifecycleErrorCode.CONFIGURATION_REJECTED: 422,
        AgentLifecycleErrorCode.RUNTIME_NOT_FOUND: 502,
        AgentLifecycleErrorCode.RUNTIME_UNAVAILABLE: 503,
        AgentLifecycleErrorCode.TIMEOUT: 504,
        AgentLifecycleErrorCode.READINESS_TIMEOUT: 504,
        AgentLifecycleErrorCode.CLEANUP_FAILED: 503,
        AgentLifecycleErrorCode.SESSION_FAILED: 502,
        AgentLifecycleErrorCode.OPERATION_FAILED: 502,
        AgentLifecycleErrorCode.MESSAGE_INVALID: 422,
        AgentLifecycleErrorCode.HISTORY_LIMIT: 409,
        AgentLifecycleErrorCode.INFERENCE_UNAVAILABLE: 503,
        AgentLifecycleErrorCode.TOOL_FAILED: 502,
        AgentLifecycleErrorCode.INTERACTION_FAILED: 502,
    }
    messages = {
        AgentLifecycleErrorCode.NOT_FOUND: "Agent not found",
        AgentLifecycleErrorCode.INVALID_STATE: "Agent state does not allow the operation",
        AgentLifecycleErrorCode.CONFLICT: "Agent operation conflicts with existing state",
        AgentLifecycleErrorCode.CONFIGURATION_REJECTED: "Agent configuration was rejected",
        AgentLifecycleErrorCode.RUNTIME_NOT_FOUND: "Agent runtime could not be found",
        AgentLifecycleErrorCode.RUNTIME_UNAVAILABLE: "Agent runtime is unavailable",
        AgentLifecycleErrorCode.TIMEOUT: "Agent operation timed out",
        AgentLifecycleErrorCode.READINESS_TIMEOUT: "Agent readiness timed out",
        AgentLifecycleErrorCode.CLEANUP_FAILED: "Agent cleanup did not complete",
        AgentLifecycleErrorCode.SESSION_FAILED: "Agent session operation failed",
        AgentLifecycleErrorCode.OPERATION_FAILED: "Agent operation failed",
        AgentLifecycleErrorCode.MESSAGE_INVALID: "Message or history query is invalid",
        AgentLifecycleErrorCode.HISTORY_LIMIT: "Conversation limit reached",
        AgentLifecycleErrorCode.INFERENCE_UNAVAILABLE: "Agent inference is unavailable",
        AgentLifecycleErrorCode.TOOL_FAILED: "Agent tool operation failed",
        AgentLifecycleErrorCode.INTERACTION_FAILED: "Agent conversation requires recovery",
    }
    return _error(
        statuses[exception.code],
        exception.code.value,
        messages[exception.code],
        operation=exception.operation,
        agent_id=(exception.agent_id.value if exception.agent_id is not None else None),
        state=exception.state,
        retryable=exception.retryable,
        interaction_code=exception.interaction_code,
    )


AgentPath = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]


def create_application(composition: ApplicationComposition) -> FastAPI:
    """Create an app from explicit dependencies without opening external services."""

    if (
        composition.settings is None
        or composition.runtime is None
        or composition.interaction is None
        or composition.lifecycle is None
        or composition.chat is None
    ):
        raise ValueError(
            "application composition requires settings, both ports and lifecycle service"
        )
    settings = composition.settings
    lifecycle = composition.lifecycle
    chat = composition.chat
    assert settings is not None
    assert lifecycle is not None
    assert chat is not None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await composition.close()

    app = FastAPI(
        title="Universal Agent Runtime",
        version="0.1.0",
        description="Agent lifecycle, JSON chat and turn-bound SSE with committed response content.",
        lifespan=lifespan,
    )
    app.state.composition = composition
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(AgentLifecycleFailure, _lifecycle_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    errors: dict[int | str, dict[str, Any]] = {
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    }

    @app.get("/healthz", response_model=HealthResponse, responses=errors)
    async def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/readyz", response_model=ReadinessResponse, responses=errors)
    async def readyz() -> ReadinessResponse:
        return ReadinessResponse(
            status="ready", runtime_driver=settings.runtime_driver.value
        )

    lifecycle_errors: dict[int | str, dict[str, Any]] = {
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    }

    @app.post(
        "/agents",
        response_model=AgentResponse,
        status_code=status.HTTP_201_CREATED,
        responses=lifecycle_errors,
    )
    async def create_agent(
        request: CreateAgentRequest, response: Response
    ) -> AgentResponse:
        result = await lifecycle.create(
            CreateAgentCommand(
                request.request_id, tuple(request.skills), tuple(request.tools)
            )
        )
        if not result.created:
            response.status_code = status.HTTP_200_OK
        return AgentResponse.from_record(result.agent)

    @app.get(
        "/agents/{agent_id}",
        response_model=AgentResponse,
        responses=lifecycle_errors,
    )
    async def inspect_agent(agent_id: AgentPath) -> AgentResponse:
        return AgentResponse.from_record(lifecycle.inspect(AgentId(agent_id)))

    @app.post(
        "/agents/{agent_id}/start",
        response_model=AgentResponse,
        responses=lifecycle_errors,
    )
    async def start_agent(agent_id: AgentPath) -> AgentResponse:
        return AgentResponse.from_record(await lifecycle.start(AgentId(agent_id)))

    @app.post(
        "/agents/{agent_id}/stop",
        response_model=AgentResponse,
        responses=lifecycle_errors,
    )
    async def stop_agent(agent_id: AgentPath) -> AgentResponse:
        return AgentResponse.from_record(await lifecycle.stop(AgentId(agent_id)))

    @app.delete(
        "/agents/{agent_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        responses=lifecycle_errors,
    )
    async def delete_agent(agent_id: AgentPath) -> Response:
        await lifecycle.delete(AgentId(agent_id))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post(
        "/agents/{agent_id}/messages",
        response_model=MessagesResponse,
        status_code=201,
        responses=lifecycle_errors,
    )
    async def send_message(
        agent_id: AgentPath, request: SendMessageRequest
    ) -> MessagesResponse:
        messages = await chat.send(AgentId(agent_id), request.content)
        return MessagesResponse(
            agent_id=agent_id,
            messages=[MessageResponse.from_message(message) for message in messages],
        )

    @app.get(
        "/agents/{agent_id}/messages",
        response_model=MessagesResponse,
        responses=lifecycle_errors,
    )
    async def get_messages(
        agent_id: AgentPath,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int | None, Query(ge=1)] = None,
    ) -> MessagesResponse:
        page = chat.history(AgentId(agent_id), after=after, limit=limit)
        return MessagesResponse(
            agent_id=agent_id,
            messages=[
                MessageResponse.from_message(message) for message in page.messages
            ],
            next_after=page.next_after,
        )

    @app.post(
        "/agents/{agent_id}/messages/stream",
        status_code=200,
        response_class=TurnEventResponse,
        responses={
            **lifecycle_errors,
            200: {
                "description": "SSE started, then committed content/completed or error; comments keep the connection alive. No token deltas or replay.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            },
        },
        openapi_extra={"x-event-schema": StreamEvent.model_json_schema()},
    )
    async def stream_message(
        agent_id: AgentPath, request: SendMessageRequest, connection: Request
    ) -> Response:
        if "last-event-id" in connection.headers:
            return _error(
                409,
                "replay_not_supported",
                "Inspect message history before submitting a new turn",
            )
        turn = chat.begin(AgentId(agent_id), request.content)
        return TurnEventResponse(
            turn,
            heartbeat_seconds=settings.stream_heartbeat_seconds,
            send_timeout_seconds=settings.stream_send_timeout_seconds,
        )

    @app.post(
        "/ag-ui/agents/{agent_id}/run",
        status_code=200,
        response_class=AGUIEventResponse,
        responses={
            200: {
                "description": "AG-UI SSE run lifecycle. The response is buffered until the existing Agent turn commits.",
                "content": {"text/event-stream": {"schema": {"type": "string"}}},
            }
        },
        openapi_extra={
            "x-ag-ui-events": [
                "RUN_STARTED",
                "TEXT_MESSAGE_START",
                "TEXT_MESSAGE_CONTENT",
                "TEXT_MESSAGE_END",
                "RUN_FINISHED",
                "RUN_ERROR",
            ]
        },
    )
    async def run_ag_ui_agent(agent_id: AgentPath, request: Request) -> Response:
        """Expose one existing Agent turn through the AG-UI run/event contract."""

        try:
            raw = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return AGUIEventResponse(
                ag_ui_error_events(
                    thread_id=None,
                    run_id=None,
                    code="request_invalid",
                    message="Run input validation failed",
                )
            )
        input, thread_id, run_id = parse_run_input(raw)
        if input is None:
            return AGUIEventResponse(
                ag_ui_error_events(
                    thread_id=thread_id,
                    run_id=run_id,
                    code="request_invalid",
                    message="Run input validation failed",
                )
            )
        try:
            turn = begin_run(chat, agent_id, input)
        except AgentLifecycleFailure as failure:
            code, message = safe_failure(failure)
            return AGUIEventResponse(
                ag_ui_error_events(
                    thread_id=input.threadId,
                    run_id=input.runId,
                    code=code,
                    message=message,
                )
            )
        return AGUIEventResponse(
            ag_ui_events(
                turn,
                thread_id=input.threadId,
                run_id=input.runId,
                heartbeat_seconds=settings.stream_heartbeat_seconds,
            )
        )

    return app


def create_application_from_environment(
    environment: Mapping[str, str] | None = None,
) -> FastAPI:
    """Validate deployment configuration and construct the selected adapters."""

    return create_application(
        compose_application(ApplicationSettings.from_environment(environment))
    )
