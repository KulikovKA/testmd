"""Transport DTOs and routes for tasks; streaming is supplied by existing AG-UI."""

from dataclasses import asdict
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from universal_agent_runtime.ag_ui import (
    AGUIEventResponse,
    ag_ui_error_events,
    ag_ui_events,
)
from universal_agent_runtime.application.development_workflow import DevelopmentWorkflow
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.domain.development_task import (
    BuildSystem,
    DevelopmentFailure,
    DevelopmentRequest,
)
from universal_agent_runtime.domain.identifiers import AgentId


class DevelopmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    specification: str = Field(min_length=1, max_length=8000)
    build_system: Literal["maven", "gradle"] = "maven"
    branch: str = "main"
    repository_url: str | None = None
    base_branch: str = "master"
    local_only: bool = False
    publish: bool = False
    max_fix_attempts: int = Field(default=2, ge=0, le=3)


class ClarificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str = Field(min_length=1, max_length=4000)


class DevelopmentRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    threadId: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    runId: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def task_response(task) -> dict:
    return {
        "task_id": task.task_id,
        "agent_id": task.request.agent_id.value,
        "state": task.state.value,
        "version": task.version,
        "questions": task.questions,
        "plan": task.plan,
        "fix_attempts": task.fix_attempts,
        "cancel_requested": task.cancel_requested,
        "failure_code": task.failure_code,
        "result": asdict(task.result) if task.result else None,
    }


def register_development_routes(
    app: FastAPI, workflow: DevelopmentWorkflow | None, settings: ApplicationSettings
) -> None:
    def require() -> DevelopmentWorkflow:
        if workflow is None:
            raise DevelopmentFailure("agent_unavailable")
        return workflow

    def get(task_id):
        try:
            return require().service.get(task_id)
        except ValueError:
            raise DevelopmentFailure("invalid_request") from None

    async def failure_handler(_request, exception):
        code = exception.code
        status = (
            404
            if code == "task_not_found"
            else 409
            if code in {"task_conflict", "clarification_required"}
            else 503
            if code == "agent_unavailable"
            else 422
        )
        return JSONResponse(
            {"error": {"code": code, "message": "Development task request failed"}},
            status_code=status,
        )

    app.add_exception_handler(DevelopmentFailure, failure_handler)

    @app.post("/agents/{agent_id}/development-tasks", status_code=201)
    async def create(agent_id: str, payload: DevelopmentInput):
        try:
            request = DevelopmentRequest(
                AgentId(agent_id),
                payload.specification,
                BuildSystem(payload.build_system),
                payload.branch,
                None,
                payload.publish,
                payload.max_fix_attempts,
                payload.repository_url,
                payload.base_branch,
            )
        except ValueError:
            raise DevelopmentFailure("invalid_request") from None
        workflow = require()
        if (payload.repository_url is None and not payload.local_only) or (
            payload.repository_url is not None and payload.local_only
        ):
            raise DevelopmentFailure("invalid_request")
        return task_response(workflow.create(request))

    @app.get("/development-tasks/{task_id}")
    async def read(task_id: str):
        return task_response(get(task_id))

    @app.get("/development-tasks/{task_id}/trace")
    async def trace(task_id: str):
        task = get(task_id)
        return {
            "task_id": task.task_id,
            "agent_id": task.request.agent_id.value,
            "state": task.state.value,
            "events": [event.to_dict() for event in task.trace],
            "summary": task.trace[-1].summary if task.trace else "Задача создана",
            "failure_code": task.failure_code,
        }

    @app.post("/development-tasks/{task_id}/clarifications")
    async def clarify(task_id: str, payload: ClarificationInput):
        get(task_id)
        return task_response(require().clarify(task_id, payload.answer))

    @app.post("/development-tasks/{task_id}/cancel")
    async def cancel(task_id: str):
        get(task_id)
        return task_response(require().service.cancel(task_id))

    @app.post(
        "/ag-ui/development-tasks/{task_id}/run", response_class=AGUIEventResponse
    )
    async def run(task_id: str, payload: DevelopmentRunInput):
        try:
            get(task_id)
            turn = require().begin(task_id)
        except DevelopmentFailure as failure:
            return AGUIEventResponse(
                ag_ui_error_events(
                    thread_id=payload.threadId,
                    run_id=payload.runId,
                    code=failure.code,
                    message="Development task cannot start",
                )
            )
        return AGUIEventResponse(
            ag_ui_events(
                turn,
                thread_id=payload.threadId,
                run_id=payload.runId,
                heartbeat_seconds=settings.stream_heartbeat_seconds,
            ),
            send_timeout_seconds=settings.stream_send_timeout_seconds,
            turn=turn,
        )
