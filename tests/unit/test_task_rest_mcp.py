import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast

import pytest

from universal_agent_runtime.adapters.qwen_session import QWEN_IMAGE

SERVER = (
    Path(__file__).parents[2]
    / "src"
    / "universal_agent_runtime"
    / "adapters"
    / "task_rest_mcp_server.mjs"
)


class TaskApi(BaseHTTPRequestHandler):
    calls: ClassVar[list[tuple[str, str, dict[str, str], dict[str, Any] | None]]] = []
    records: ClassVar[dict[str, dict[str, Any]]] = {}
    next_id: ClassVar[int] = 1
    response_bytes: ClassVar[bytes | None] = None
    forced_status: ClassVar[int | None] = None
    response_delay_seconds: ClassVar[float] = 0

    def log_message(self, _: str, *args: object) -> None:
        return

    def _body(self) -> dict[str, Any] | None:
        size = int(self.headers.get("content-length", "0"))
        return json.loads(self.rfile.read(size)) if size else None

    def _task(self, task_id: str) -> dict[str, Any] | None:
        return self.records.get(task_id)

    def _send(self, status: int, payload: object) -> None:
        if self.response_delay_seconds:
            time.sleep(self.response_delay_seconds)
        raw = (
            self.response_bytes
            if self.response_bytes is not None
            else json.dumps(payload).encode("utf-8")
        )
        self.send_response(self.forced_status or status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record_call(self, body: dict[str, Any] | None) -> None:
        self.calls.append(
            (
                self.command,
                self.path,
                {key.lower(): value for key, value in self.headers.items()},
                body,
            )
        )

    def do_GET(self) -> None:
        self._record_call(None)
        task = self._task(self.path.rsplit("/", 1)[-1])
        self._send(200, task) if task else self._send(404, {"error": {}})

    def do_POST(self) -> None:
        body = self._body()
        self._record_call(body)
        assert body is not None
        parent_id = None
        if self.path.endswith("/subtasks"):
            parent_id = self.path.split("/")[2]
            if parent_id not in self.records:
                self._send(404, {"error": {}})
                return
        task_id = f"task-{self.next_id:04d}"
        type(self).next_id += 1
        task = {
            "id": task_id,
            "title": body["title"],
            "description": body["description"],
            "status": "open",
            "parent_id": parent_id,
            "subtask_ids": [],
            "version": 1,
        }
        self.records[task_id] = task
        self._send(201, task)

    def do_PATCH(self) -> None:
        body = self._body()
        self._record_call(body)
        assert body is not None
        task = self._task(self.path.rsplit("/", 1)[-1])
        if task is None:
            self._send(404, {"error": {}})
            return
        if body["expected_version"] != task["version"]:
            self._send(409, {"error": {}})
            return
        task.update(
            {key: value for key, value in body.items() if key != "expected_version"}
        )
        task["version"] += 1
        self._send(200, task)


@contextmanager
def task_api() -> Iterator[str]:
    TaskApi.calls = []
    TaskApi.records = {}
    TaskApi.next_id = 1
    TaskApi.response_bytes = None
    TaskApi.forced_status = None
    TaskApi.response_delay_seconds = 0
    server = ThreadingHTTPServer(("0.0.0.0", 0), TaskApi)
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://host.docker.internal:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class McpClient:
    def __init__(self, environment: dict[str, str]) -> None:
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--entrypoint",
            "node",
            "-v",
            f"{SERVER.parent}:/tool:ro",
        ]
        for name, value in environment.items():
            command.extend(("-e", f"{name}={value}"))
        command.extend((QWEN_IMAGE, "/tool/task_rest_mcp_server.mjs"))
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env={**os.environ, **environment},
        )
        assert self._process.stdin is not None
        assert self._process.stdout is not None
        self._input = self._process.stdin
        self._output = self._process.stdout
        self._next_id = 1

    def request(self, method: str, params: object) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._input.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            + "\n"
        )
        self._input.flush()
        while True:
            line = self._output.readline()
            assert line, self._stderr()
            response = json.loads(line)
            if response.get("id") == request_id:
                if not isinstance(response, dict):
                    raise AssertionError("MCP response is not an object")
                return cast(dict[str, Any], response)

    def _stderr(self) -> str:
        assert self._process.stderr is not None
        return self._process.stderr.read()

    def close(self) -> None:
        self._process.terminate()
        self._process.wait(timeout=5)


@pytest.fixture
def mcp() -> Iterator[McpClient]:
    with task_api() as endpoint:
        client = McpClient(
            {
                "UAR_TASK_API_BASE_URL": endpoint,
                "UAR_AGENT_TOOL_CAPABILITIES": "get_task,create_task,create_subtask,update_task",
                "UAR_TASK_API_TOKEN": "test-task-token",
                "UAR_TASK_API_TIMEOUT_MS": "1000",
            }
        )
        try:
            yield client
        finally:
            client.close()


def result(response: dict[str, Any]) -> dict[str, Any]:
    parsed = json.loads(response["result"]["content"][0]["text"])
    if not isinstance(parsed, dict):
        raise TypeError("MCP tool result is not an object")
    return cast(dict[str, Any], parsed)


def test_discovery_returns_only_exactly_enabled_tools() -> None:
    with task_api() as endpoint:
        client = McpClient(
            {
                "UAR_TASK_API_BASE_URL": endpoint,
                "UAR_AGENT_TOOL_CAPABILITIES": "get_task,update_task,not_a_task_tool",
            }
        )
        try:
            initialized = client.request(
                "initialize",
                {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {}},
            )
            assert initialized["result"]["capabilities"] == {"tools": {}}
            listed = client.request("tools/list", {})
            assert [tool["name"] for tool in listed["result"]["tools"]] == [
                "get_task",
                "update_task",
            ]
        finally:
            client.close()


def test_mcp_protocol_performs_each_fixed_task_operation(mcp: McpClient) -> None:
    created = result(
        mcp.request(
            "tools/call", {"name": "create_task", "arguments": {"title": "Parent"}}
        )
    )
    child = result(
        mcp.request(
            "tools/call",
            {
                "name": "create_subtask",
                "arguments": {"task_id": created["id"], "title": "Child"},
            },
        )
    )
    fetched = result(
        mcp.request(
            "tools/call", {"name": "get_task", "arguments": {"task_id": created["id"]}}
        )
    )
    updated = result(
        mcp.request(
            "tools/call",
            {
                "name": "update_task",
                "arguments": {
                    "task_id": child["id"],
                    "expected_version": child["version"],
                    "status": "in_progress",
                },
            },
        )
    )

    assert fetched["id"] == created["id"]
    assert updated["status"] == "in_progress"
    assert [(method, path) for method, path, _, _ in TaskApi.calls] == [
        ("POST", "/tasks"),
        ("POST", f"/tasks/{created['id']}/subtasks"),
        ("GET", f"/tasks/{created['id']}"),
        ("PATCH", f"/tasks/{child['id']}"),
    ]
    assert all(
        headers.get("authorization") == "Bearer test-task-token"
        for _, _, headers, _ in TaskApi.calls
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_task", {"task_id": "task-0001?method=DELETE"}),
        ("create_task", {"title": "safe", "headers": {"authorization": "stolen"}}),
        (
            "update_task",
            {"task_id": "task-0001", "expected_version": 1, "method": "DELETE"},
        ),
        ("unknown", {}),
    ],
)
def test_unvalidated_escape_and_injection_inputs_never_reach_http_service(
    mcp: McpClient, name: str, arguments: dict[str, object]
) -> None:
    response = mcp.request("tools/call", {"name": name, "arguments": arguments})
    assert response["result"]["isError"] is True
    assert TaskApi.calls == []


def test_denied_operation_and_secret_are_redacted_from_diagnostics() -> None:
    with task_api() as endpoint:
        secret = "not-to-be-disclosed"
        client = McpClient(
            {
                "UAR_TASK_API_BASE_URL": endpoint,
                "UAR_AGENT_TOOL_CAPABILITIES": "get_task",
                "UAR_TASK_API_TOKEN": secret,
            }
        )
        try:
            denied = client.request(
                "tools/call", {"name": "create_task", "arguments": {"title": "x"}}
            )
            missing = client.request(
                "tools/call",
                {"name": "get_task", "arguments": {"task_id": "task-9999"}},
            )
            assert result(denied)["error"]["code"] == "capability_denied"
            assert result(missing)["error"]["code"] == "not_found"
            assert secret not in json.dumps([denied, missing])
        finally:
            client.close()


@pytest.mark.parametrize(
    ("forced_status", "response_bytes", "delay", "timeout", "expected"),
    [
        (401, None, 0, "1000", "authentication_failed"),
        (500, None, 0, "1000", "service_failure"),
        (None, b"x" * 70_000, 0, "1000", "service_failure"),
        (None, None, 0.05, "1", "timeout"),
    ],
    ids=["auth", "service", "oversized", "timeout"],
)
def test_service_failures_return_redacted_diagnostics(
    forced_status: int | None,
    response_bytes: bytes | None,
    delay: float,
    timeout: str,
    expected: str,
) -> None:
    with task_api() as endpoint:
        TaskApi.forced_status = forced_status
        TaskApi.response_bytes = response_bytes
        TaskApi.response_delay_seconds = delay
        client = McpClient(
            {
                "UAR_TASK_API_BASE_URL": endpoint,
                "UAR_AGENT_TOOL_CAPABILITIES": "get_task",
                "UAR_TASK_API_TOKEN": "diagnostic-test-token",
                "UAR_TASK_API_TIMEOUT_MS": timeout,
            }
        )
        try:
            response = client.request(
                "tools/call",
                {"name": "get_task", "arguments": {"task_id": "task-0001"}},
            )
            assert result(response)["error"]["code"] == expected
            assert "diagnostic-test-token" not in json.dumps(response)
        finally:
            client.close()
