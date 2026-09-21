"""FastAPI boundary for the Agent Orchestrator lifecycle API."""

import json
import re
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Path, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException, MultiPartParser

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
from universal_agent_runtime.application.ports.interaction_values import (
    SessionDebugSnapshot,
)
from universal_agent_runtime.application.ports.llm_turns import (
    MAX_TURNS_PAGE,
    LLMTurnsFailure,
)
from universal_agent_runtime.application.ports.runtime_values import OperationOptions
from universal_agent_runtime.application.ports.skill_store import (
    MAX_ARCHIVE_BYTES,
    SkillDescriptor,
    SkillInstallRequest,
    SkillStoreFailure,
)
from universal_agent_runtime.application.ports.workspace_inventory import (
    WorkspaceEntry,
    WorkspaceInventory,
)
from universal_agent_runtime.composition import (
    ApplicationComposition,
    compose_application,
)
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.development_http import register_development_routes
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


class SkillResponse(BaseModel):
    id: str
    version: str
    summary: str
    source_type: Literal["builtin", "archive", "git"]
    tool_capabilities: list[str]
    mutation_tool_capabilities: list[str]

    @classmethod
    def from_descriptor(cls, descriptor: SkillDescriptor) -> "SkillResponse":
        return cls(
            id=descriptor.identifier,
            version=descriptor.version,
            summary=descriptor.summary,
            source_type=cast(Literal["builtin", "archive", "git"], descriptor.source_type),
            tool_capabilities=list(descriptor.tool_capabilities),
            mutation_tool_capabilities=list(descriptor.mutation_tool_capabilities),
        )


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


class AgentDebugIdentityResponse(BaseModel):
    agent_id: str
    workspace_id: str
    session_id: str
    state: AgentLifecycleState


class QwenDebugResponse(BaseModel):
    model: str | None
    native_session_id: str | None
    completed_turns: int | None
    transcript_present: bool


class WorkspaceDebugResponse(BaseModel):
    skills: list[str]
    mcp_server_present: bool


class TurnTelemetryResponse(BaseModel):
    duration_ms: int | float | None = None
    ttft_ms: int | float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thoughts_tokens: int | None = None
    total_tokens: int | None = None


class LastTurnDebugResponse(BaseModel):
    user_message_present: bool
    assistant_message_present: bool
    qwen_events: dict[str, int]
    telemetry: TurnTelemetryResponse
    mcp_tool_names: list[str]


class AgentDebugReportResponse(BaseModel):
    agent: AgentDebugIdentityResponse
    runtime: RuntimeObservationResponse | None
    configuration: AgentConfigurationResponse
    qwen: QwenDebugResponse
    workspace: WorkspaceDebugResponse
    last_turn: LastTurnDebugResponse | None
    failure: AgentFailureResponse | None
    conversation_recovery_required: bool

    @classmethod
    def from_observation(
        cls, record: AgentRecord, snapshot: SessionDebugSnapshot
    ) -> "AgentDebugReportResponse":
        agent = AgentResponse.from_record(record)
        user_present = snapshot.last_user_message_present or any(
            message.role == "user" for message in record.messages
        )
        assistant_present = snapshot.last_assistant_message_present or any(
            message.role == "assistant" for message in record.messages
        )
        return cls(
            agent=AgentDebugIdentityResponse(
                agent_id=agent.agent_id,
                workspace_id=agent.workspace_id,
                session_id=agent.session_id,
                state=agent.state,
            ),
            runtime=agent.runtime,
            configuration=agent.configuration,
            qwen=QwenDebugResponse(
                model=snapshot.model,
                native_session_id=snapshot.native_session_id,
                completed_turns=snapshot.completed_turns,
                transcript_present=snapshot.transcript_present,
            ),
            workspace=WorkspaceDebugResponse(
                skills=list(record.configuration.skills),
                mcp_server_present=snapshot.mcp_server_present,
            ),
            last_turn=(
                LastTurnDebugResponse(
                    user_message_present=user_present,
                    assistant_message_present=assistant_present,
                    qwen_events={
                        name: snapshot.event_counts.get(name, 0)
                        for name in ("user", "system", "assistant", "tool_call", "tool_result")
                    },
                    telemetry=TurnTelemetryResponse.model_validate(snapshot.telemetry),
                    mcp_tool_names=list(snapshot.mcp_tool_names),
                )
                if user_present or assistant_present
                else None
            ),
            failure=agent.failure,
            conversation_recovery_required=agent.conversation_recovery_required,
        )


class WorkspaceFileResponse(BaseModel):
    path: str
    type: Literal["file", "directory"]
    category: str
    size_bytes: int | None = None

    @classmethod
    def from_entry(cls, entry: WorkspaceEntry) -> "WorkspaceFileResponse":
        return cls(
            path=entry.path,
            type=entry.type,
            category=entry.category,
            size_bytes=entry.size_bytes,
        )


class WorkspaceFileSummaryResponse(BaseModel):
    directories: int
    files: int
    skills: int
    transcripts: int
    mcp_server_present: bool


class AgentFilesResponse(BaseModel):
    agent_id: str
    state: AgentLifecycleState
    root: Literal["/workspace"] = "/workspace"
    available: bool
    reason: Literal["workspace_not_available"] | None = None
    files: list[WorkspaceFileResponse]
    truncated: bool
    summary: WorkspaceFileSummaryResponse

    @classmethod
    def from_inventory(
        cls, record: AgentRecord, inventory: WorkspaceInventory
    ) -> "AgentFilesResponse":
        entries = inventory.files
        skills = {
            entry.path.split("/")[2]
            for entry in entries
            if entry.path.startswith(".agent/skills/")
            and len(entry.path.split("/")) >= 3
        }
        return cls(
            agent_id=record.agent_id.value,
            state=record.state,
            available=inventory.available,
            reason=None if inventory.available else "workspace_not_available",
            files=[WorkspaceFileResponse.from_entry(entry) for entry in entries],
            truncated=inventory.truncated,
            summary=WorkspaceFileSummaryResponse(
                directories=sum(entry.type == "directory" for entry in entries),
                files=sum(entry.type == "file" for entry in entries),
                skills=len(skills),
                transcripts=sum(
                    entry.category == "qwen_transcript"
                    and entry.type == "file"
                    and entry.path.endswith(".jsonl")
                    for entry in entries
                ),
                mcp_server_present=any(
                    entry.path == ".uar-tools/task_rest_mcp_server.mjs"
                    for entry in entries
                ),
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


async def _skill_exception_handler(_: Request, exception: Exception) -> JSONResponse:
    assert isinstance(exception, SkillStoreFailure)
    responses = {
        "skill_archive_invalid": (422, "Skill archive is invalid"),
        "skill_archive_too_large": (413, "Skill archive exceeds limits"),
        "skill_already_exists": (409, "Skill already exists"),
        "skill_source_unavailable": (501, "Git Skill source is not available in this deployment"),
        "skill_registry_unavailable": (503, "Skill registry is unavailable"),
        "skill_request_invalid": (422, "Skill request is invalid"),
    }
    status_code, message = responses.get(
        exception.code, (500, "Skill operation failed")
    )
    return _error(status_code, exception.code, message)


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
        AgentLifecycleErrorCode.SKILL_UNAVAILABLE: 422,
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
        AgentLifecycleErrorCode.SKILL_UNAVAILABLE: "Selected Skill is unavailable",
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
SkillPath = AgentPath


def _valid_skill_source_fields(
    source_type: str,
    archive: UploadFile | None,
    repository_url: str | None,
    revision: str | None,
    path: str | None,
) -> bool:
    if source_type == "archive":
        return archive is not None and not any((repository_url, revision, path))
    if source_type != "git" or archive is not None:
        return False
    if not repository_url or not revision or path is None:
        return False
    try:
        parsed = urlsplit(repository_url)
        hostname = parsed.hostname
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and len(repository_url) <= 2048
        and re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", revision) is not None
        and len(path) <= 256
        and not path.startswith("/")
        and "\\" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
    )


def create_application(composition: ApplicationComposition) -> FastAPI:
    """Create an app from explicit dependencies without opening external services."""

    if (
        composition.settings is None
        or composition.runtime is None
        or composition.interaction is None
        or composition.lifecycle is None
        or composition.chat is None
        or composition.skills is None
    ):
        raise ValueError(
            "application composition requires settings, both ports and lifecycle service"
        )
    settings = composition.settings
    lifecycle = composition.lifecycle
    chat = composition.chat
    skills = composition.skills
    assert settings is not None
    assert lifecycle is not None
    assert chat is not None
    assert skills is not None

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
    app.add_exception_handler(SkillStoreFailure, _skill_exception_handler)
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

    skill_errors: dict[int | str, dict[str, Any]] = {
        code: {"model": ErrorResponse} for code in (404, 409, 413, 422, 501, 503)
    }

    @app.get("/skills", response_model=list[SkillResponse], responses=skill_errors)
    async def list_skills() -> list[SkillResponse]:
        return [SkillResponse.from_descriptor(item) for item in skills.list_skills()]

    @app.get("/skills/{skill_id}", response_model=SkillResponse, responses=skill_errors)
    async def get_skill(skill_id: SkillPath) -> SkillResponse | JSONResponse:
        descriptor = skills.get_skill(skill_id)
        if descriptor is None:
            return _error(404, "skill_not_found", "Skill not found")
        return SkillResponse.from_descriptor(descriptor)

    @app.post(
        "/skills",
        response_model=SkillResponse,
        status_code=201,
        responses=skill_errors,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["source_type", "skill_id"],
                            "properties": {
                                "source_type": {"type": "string", "enum": ["archive", "git"]},
                                "skill_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"},
                                "archive": {"type": "string", "format": "binary"},
                                "repository_url": {"type": "string"},
                                "revision": {"type": "string"},
                                "path": {"type": "string"},
                            },
                        }
                    }
                },
            }
        },
    )
    async def install_skill(request: Request) -> SkillResponse | JSONResponse:
        if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            return _error(422, "request_invalid", "Skill request validation failed")
        max_body = MAX_ARCHIVE_BYTES + 65_536
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > max_body:
                return _error(413, "skill_archive_too_large", "Skill archive exceeds limits")
            raw.extend(chunk)

        async def body_chunks() -> AsyncGenerator[bytes, None]:
            yield bytes(raw)

        try:
            form = await MultiPartParser(request.headers, body_chunks()).parse()
        except (MultiPartException, ValueError):
            return _error(422, "request_invalid", "Skill request validation failed")
        try:
            if any(len(form.getlist(key)) != 1 for key in form):
                return _error(422, "request_invalid", "Skill request validation failed")
            if set(form.keys()) - {
                "source_type", "skill_id", "archive", "repository_url", "revision", "path"
            }:
                return _error(422, "request_invalid", "Skill request validation failed")
            source_type = form.get("source_type")
            skill_id = form.get("skill_id")
            archive = form.get("archive")
            repository_url = form.get("repository_url")
            revision = form.get("revision")
            path = form.get("path")
            if archive is not None and not isinstance(archive, UploadFile):
                return _error(422, "request_invalid", "Skill request validation failed")
            if any(
                value is not None and not isinstance(value, str)
                for value in (repository_url, revision, path)
            ):
                return _error(422, "request_invalid", "Skill request validation failed")
            repository_text = repository_url if isinstance(repository_url, str) else None
            revision_text = revision if isinstance(revision, str) else None
            path_text = path if isinstance(path, str) else None
            if (
                not isinstance(source_type, str)
                or not isinstance(skill_id, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", skill_id) is None
                or not _valid_skill_source_fields(
                    source_type, archive, repository_text, revision_text, path_text
                )
            ):
                return _error(422, "request_invalid", "Skill request validation failed")
            payload = await archive.read(MAX_ARCHIVE_BYTES + 1) if archive else None
            descriptor = skills.install(
                SkillInstallRequest(
                    source_type=source_type,
                    archive=payload,
                    skill_id=skill_id,
                    repository_url=repository_text,
                    revision=revision_text,
                    path=path_text,
                )
            )
            return SkillResponse.from_descriptor(descriptor)
        finally:
            await form.close()

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

    @app.get(
        "/agents/{agent_id}/debug-report",
        response_model=AgentDebugReportResponse,
        responses=lifecycle_errors,
    )
    async def agent_debug_report(agent_id: AgentPath) -> AgentDebugReportResponse:
        record = lifecycle.inspect(AgentId(agent_id))
        snapshot = (
            await composition.debug_reader.debug_snapshot(record.session)
            if composition.debug_reader is not None
            else SessionDebugSnapshot()
        )
        return AgentDebugReportResponse.from_observation(record, snapshot)

    @app.get(
        "/agents/{agent_id}/files",
        response_model=AgentFilesResponse,
        responses=lifecycle_errors,
    )
    async def agent_files(agent_id: AgentPath) -> AgentFilesResponse:
        record = lifecycle.inspect(AgentId(agent_id))
        inventory = (
            await composition.workspace_reader.workspace_inventory(
                record.runtime_handle,
                options=OperationOptions(settings.agent_operation_timeout_seconds),
            )
            if composition.workspace_reader is not None
            and record.runtime_handle is not None
            else WorkspaceInventory(False)
        )
        return AgentFilesResponse.from_inventory(record, inventory)

    @app.get("/agents/{agent_id}/llm-turns")
    async def agent_llm_turns(
        agent_id: AgentPath,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=10, ge=1, le=MAX_TURNS_PAGE),
    ):
        record = lifecycle.inspect(AgentId(agent_id))
        try:
            if composition.llm_turns_reader is None:
                raise LLMTurnsFailure("llm_turns_unavailable")
            page = await composition.llm_turns_reader.llm_turns(
                record.session, after=after, limit=limit
            )
            return {"agent_id": agent_id, **asdict(page)}
        except LLMTurnsFailure as failure:
            codes = {
                "llm_turns_unavailable": 503,
                "llm_turns_busy": 409,
                "llm_session_not_found": 404,
                "llm_transcript_not_found": 404,
                "llm_transcript_invalid": 502,
                "llm_turns_limit": 413,
            }
            return _error(
                codes[failure.code], failure.code, "LLM turns are unavailable"
            )

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
                "description": "AG-UI SSE with provisional assistant text deltas; TEXT_MESSAGE_END and RUN_FINISHED follow a successful turn commit.",
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
            ),
            send_timeout_seconds=settings.stream_send_timeout_seconds,
            turn=turn,
        )

    register_development_routes(app, composition.development, settings)
    return app


def create_application_from_environment(
    environment: Mapping[str, str] | None = None,
) -> FastAPI:
    """Validate deployment configuration and construct the selected adapters."""

    return create_application(
        compose_application(ApplicationSettings.from_environment(environment))
    )
