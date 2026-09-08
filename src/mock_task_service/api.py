"""FastAPI transport for the standalone mock Task service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mock_task_service.models import Task
from mock_task_service.schemas import (
    CreateTaskRequest,
    ErrorBody,
    ErrorResponse,
    TaskResponse,
    UpdateTaskRequest,
)
from mock_task_service.store import (
    InMemoryTaskStore,
    TaskConflictError,
    TaskNotFoundError,
)

TaskId = Annotated[str, Path(pattern=r"^task-[0-9]{4,}$")]


def _response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        title=task.title,
        description=task.description,
        status=task.status,
        parent_id=task.parent_id,
        subtask_ids=list(task.subtask_ids),
        version=task.version,
    )


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app(store: InMemoryTaskStore | None = None) -> FastAPI:
    """Create an app with isolated state unless an explicit test store is supplied."""
    app_store = store or InMemoryTaskStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title="Mock Task REST API", version="1.0.0", lifespan=lifespan)

    def get_store() -> InMemoryTaskStore:
        return app_store

    Store = Annotated[InMemoryTaskStore, Depends(get_store)]

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_request",
            "request validation failed",
        )

    @app.exception_handler(TaskNotFoundError)
    async def not_found(_: Request, __: TaskNotFoundError) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, "task_not_found", "task was not found")

    @app.exception_handler(TaskConflictError)
    async def conflict(_: Request, __: TaskConflictError) -> JSONResponse:
        return _error(
            status.HTTP_409_CONFLICT,
            "version_conflict",
            "expected_version does not match",
        )

    error_responses: dict[int | str, dict[str, Any]] = {
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    }

    @app.get("/tasks/{task_id}", response_model=TaskResponse, responses=error_responses)
    def get_task(task_id: TaskId, task_store: Store) -> TaskResponse:
        return _response(task_store.get(task_id))

    @app.post(
        "/tasks",
        response_model=TaskResponse,
        status_code=status.HTTP_201_CREATED,
        responses=error_responses,
    )
    def create_task(payload: CreateTaskRequest, task_store: Store) -> TaskResponse:
        return _response(
            task_store.create(title=payload.title, description=payload.description)
        )

    @app.post(
        "/tasks/{task_id}/subtasks",
        response_model=TaskResponse,
        status_code=status.HTTP_201_CREATED,
        responses=error_responses,
    )
    def create_subtask(
        task_id: TaskId, payload: CreateTaskRequest, task_store: Store
    ) -> TaskResponse:
        return _response(
            task_store.create(
                title=payload.title, description=payload.description, parent_id=task_id
            )
        )

    @app.patch(
        "/tasks/{task_id}", response_model=TaskResponse, responses=error_responses
    )
    def update_task(
        task_id: TaskId, payload: UpdateTaskRequest, task_store: Store
    ) -> TaskResponse:
        return _response(
            task_store.update(
                task_id,
                expected_version=payload.expected_version,
                title=payload.title,
                description=payload.description,
                status=payload.status,
            )
        )

    return app


app = create_app()
