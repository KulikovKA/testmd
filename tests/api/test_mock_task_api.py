"""Public contract tests for every mock Task operation and failure response."""

import asyncio
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

from mock_task_service import create_app


class ApiClient:
    def __init__(self, app: FastAPI) -> None:
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
    ) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, path, json=json)

        return asyncio.run(send())

    def get(self, path: str) -> httpx.Response:
        return self.request("GET", path)

    def post(self, path: str, *, json: dict[str, object]) -> httpx.Response:
        return self.request("POST", path, json=json)

    def patch(self, path: str, *, json: dict[str, object]) -> httpx.Response:
        return self.request("PATCH", path, json=json)


@pytest.fixture
def client() -> ApiClient:
    return ApiClient(create_app())


def create_task(client: ApiClient, title: str = "Parent") -> dict[str, Any]:
    response = client.post(
        "/tasks", json={"title": title, "description": "Synthetic task"}
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def assert_error(response: httpx.Response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert body["error"]["code"] == code
    assert set(body["error"]) == {"code", "message"}


def test_create_and_get_task(client: ApiClient) -> None:
    first = create_task(client, "First")
    second = create_task(client, "Second")

    assert first == {
        "id": "task-0001",
        "title": "First",
        "description": "Synthetic task",
        "status": "open",
        "parent_id": None,
        "subtask_ids": [],
        "version": 1,
    }
    assert second["id"] == "task-0002"
    response = client.get("/tasks/task-0001")
    assert response.status_code == 200
    assert response.json() == first


def test_create_subtask_updates_parent_relationship(client: ApiClient) -> None:
    parent = create_task(client)
    response = client.post(
        f"/tasks/{parent['id']}/subtasks",
        json={"title": "Child", "description": "Synthetic child"},
    )

    assert response.status_code == 201
    child = response.json()
    assert child["id"] == "task-0002"
    assert child["parent_id"] == parent["id"]
    assert child["subtask_ids"] == []
    updated_parent = client.get(f"/tasks/{parent['id']}").json()
    assert updated_parent["subtask_ids"] == [child["id"]]
    assert updated_parent["version"] == 2


def test_update_task_and_reject_stale_version(client: ApiClient) -> None:
    task = create_task(client)
    response = client.patch(
        f"/tasks/{task['id']}",
        json={
            "expected_version": 1,
            "title": "Updated",
            "description": "Updated description",
            "status": "in_progress",
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert (
        updated["title"],
        updated["description"],
        updated["status"],
        updated["version"],
    ) == (
        "Updated",
        "Updated description",
        "in_progress",
        2,
    )

    stale = client.patch(
        f"/tasks/{task['id']}",
        json={"expected_version": 1, "status": "done"},
    )
    assert_error(stale, 409, "version_conflict")
    assert client.get(f"/tasks/{task['id']}").json() == updated


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/tasks/task-9999", None),
        ("post", "/tasks/task-9999/subtasks", {"title": "Child"}),
        ("patch", "/tasks/task-9999", {"expected_version": 1, "status": "done"}),
    ],
)
def test_not_found_is_consistent(
    client: ApiClient, method: str, path: str, json: dict[str, object] | None
) -> None:
    assert_error(client.request(method, path, json=json), 404, "task_not_found")


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("post", "/tasks", {}),
        ("post", "/tasks", {"title": " "}),
        ("post", "/tasks", {"title": "Valid", "unknown": True}),
        ("get", "/tasks/not-a-task-id", None),
        ("post", "/tasks/not-a-task-id/subtasks", {"title": "Child"}),
        ("patch", "/tasks/task-0001", {"expected_version": 1}),
        ("patch", "/tasks/task-0001", {"expected_version": 0, "status": "done"}),
        ("patch", "/tasks/task-0001", {"expected_version": 1, "status": "unknown"}),
    ],
)
def test_invalid_input_has_stable_redacted_error(
    client: ApiClient, method: str, path: str, json: dict[str, object] | None
) -> None:
    response = client.request(method, path, json=json)
    assert_error(response, 422, "invalid_request")
    assert "input" not in response.text


def test_app_factories_have_isolated_state() -> None:
    first, second = ApiClient(create_app()), ApiClient(create_app())
    assert create_task(first)["id"] == "task-0001"
    assert_error(second.get("/tasks/task-0001"), 404, "task_not_found")
    assert create_task(second)["id"] == "task-0001"


def test_openapi_contains_only_the_four_task_operations(client: ApiClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/tasks", "/tasks/{task_id}", "/tasks/{task_id}/subtasks"}
    assert set(paths["/tasks"]) == {"post"}
    assert set(paths["/tasks/{task_id}"]) == {"get", "patch"}
    assert set(paths["/tasks/{task_id}/subtasks"]) == {"post"}
