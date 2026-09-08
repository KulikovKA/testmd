"""HTTP schemas owned exclusively by the mock Task service."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mock_task_service.models import TaskStatus


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateTaskRequest(StrictSchema):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class UpdateTaskRequest(StrictSchema):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: TaskStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> "UpdateTaskRequest":
        if self.title is None and self.description is None and self.status is None:
            raise ValueError("at least one mutable field is required")
        return self


class TaskResponse(StrictSchema):
    id: str
    title: str
    description: str
    status: TaskStatus
    parent_id: str | None
    subtask_ids: list[str]
    version: int


class ErrorBody(StrictSchema):
    code: str
    message: str


class ErrorResponse(StrictSchema):
    error: ErrorBody
