"""TASK-014: public HTTP to Docker/Qwen/Ollama and restricted Task tools."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from uuid import uuid4

import docker
import httpx
import pytest
import uvicorn

from mock_task_service.api import create_app as create_task_app
from mock_task_service.store import InMemoryTaskStore
from tests.integration.test_agent_lifecycle_api import _build_agent_image, _environment
from tests.runtime_support.docker_runtime import docker_available
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LOCAL_DOCKER_E2E") != "1" or not docker_available(),
    reason="requires explicit local Docker/Ollama E2E validation",
)


@dataclass
class _Server:
    server: uvicorn.Server
    thread: threading.Thread
    listener: socket.socket
    base_url: str

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.listener.close()
        assert not self.thread.is_alive()


def _start_server(app: Any, host: str) -> _Server:
    listener = socket.socket()
    listener.bind((host, 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    return _Server(server, thread, listener, f"http://127.0.0.1:{port}")


def _wait_for_state(
    client: httpx.Client, path: str, expected: str
) -> dict[str, object]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        body = cast(dict[str, object], response.json())
        if body["state"] == expected:
            return body
        time.sleep(0.05)
    pytest.fail(f"Agent did not reach {expected}")


def _sse_turn(client: httpx.Client, path: str, content: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with client.stream(
        "POST", path + "/messages/stream", json={"content": content}
    ) as response:
        assert response.status_code == 200, response.read().decode()
        for line in response.iter_lines():
            if line.startswith("data: "):
                value = json.loads(line[6:])
                assert isinstance(value, dict)
                events.append(value)
    assert [event["data"]["type"] for event in events] == [  # type: ignore[index]
        "started",
        "content",
        "completed",
    ]
    return events


def _cleanup_agent(client: httpx.Client, agent_id: str) -> None:
    path = f"/agents/{agent_id}"
    response = client.get(path)
    if response.status_code == 404:
        return
    if response.status_code == 200 and response.json()["state"] == "BUSY":
        _wait_for_state(client, path, "READY")
    client.post(path + "/stop")
    deleted = client.delete(path)
    assert deleted.status_code == 204, deleted.text


def test_local_public_task_decomposition_flow_isolated_and_cleaned(
    tmp_path: Path,
) -> None:
    _build_agent_image()
    task_store = InMemoryTaskStore()
    task_server = _start_server(create_task_app(task_store), "0.0.0.0")
    environment = _environment(tmp_path)
    inference_endpoint = os.getenv(
        "QWEN_OLLAMA_BASE_URL", environment["UAR_QWEN_BASE_URL"]
    )
    parsed = urlparse(inference_endpoint)
    task_port = urlparse(task_server.base_url).port
    assert task_port is not None
    environment.update(
        {
            "UAR_DOCKER_NETWORK_MODE": "bridge",
            "UAR_DOCKER_NETWORK_HOST": str(parsed.hostname),
            "UAR_DOCKER_NETWORK_PORT": str(
                parsed.port or (443 if parsed.scheme == "https" else 80)
            ),
            "UAR_QWEN_BASE_URL": inference_endpoint,
            "UAR_QWEN_MODEL": os.getenv("QWEN_OLLAMA_MODEL", "qwen3:1.7b"),
            "UAR_QWEN_API_KEY": os.getenv("QWEN_OLLAMA_API_KEY", "ollama"),
            "UAR_QWEN_REASONING_DIRECTIVE": "/no_think",
            "UAR_TASK_API_BASE_URL": f"http://host.docker.internal:{task_port}",
            "UAR_TASK_API_TIMEOUT_SECONDS": "10",
            "UAR_STREAM_HEARTBEAT_SECONDS": "0.1",
        }
    )
    settings = ApplicationSettings.from_environment(environment)
    orchestrator_server = _start_server(
        create_application(compose_application(settings)), "127.0.0.1"
    )
    agents: list[str] = []
    docker_client = docker.from_env()
    isolation_marker = "ISOLATION_" + uuid4().hex[:12].upper()
    try:
        with (
            httpx.Client(base_url=orchestrator_server.base_url, timeout=420) as client,
            httpx.Client(base_url=task_server.base_url, timeout=10) as task_client,
        ):
            try:
                first = client.post(
                    "/agents",
                    json={
                        "request_id": uuid4().hex,
                        "skills": ["task-decomposition"],
                        "tools": ["create_task", "create_subtask"],
                    },
                )
                second = client.post("/agents", json={"request_id": uuid4().hex})
                assert first.status_code == second.status_code == 201
                agents.extend((first.json()["agent_id"], second.json()["agent_id"]))
                assert first.json()["workspace_id"] != second.json()["workspace_id"]
                assert first.json()["session_id"] != second.json()["session_id"]

                for agent_id in agents:
                    path = f"/agents/{agent_id}"
                    assert client.post(path + "/start").status_code == 200
                    assert _wait_for_state(client, path, "READY")["state"] == "READY"

                first_path = f"/agents/{agents[0]}"
                proposal = client.post(
                    first_path + "/messages",
                    json={
                        "content": (
                            "Propose a decomposition for Release Alpha with one child Draft docs. "
                            f"Remember marker {isolation_marker}. Do not create or update Tasks."
                        )
                    },
                )
                assert proposal.status_code == 201, proposal.text
                assert task_client.get("/tasks/task-0001").status_code == 404

                revised_events = _sse_turn(
                    client,
                    first_path,
                    "Revise the proposal: replace Draft docs with Validate API. Do not write yet.",
                )
                revised_content = revised_events[1]["data"]["content"]  # type: ignore[index]
                assert "Validate API" in str(revised_content)
                assert task_client.get("/tasks/task-0001").status_code == 404

                confirmed_parent = client.post(
                    first_path + "/messages",
                    json={
                        "content": (
                            "I explicitly confirm the entire current proposal. Execute its first "
                            "write only: call create_task once with title Release Alpha. Do not "
                            "create the subtask in this turn. Return the exact Tool result record."
                        )
                    },
                )
                assert confirmed_parent.status_code == 201, confirmed_parent.text
                parent_assistant = confirmed_parent.json()["messages"][-1]["content"]
                assert "task-0001" in parent_assistant
                assert task_client.get("/tasks/task-0002").status_code == 404

                confirmed_child = client.post(
                    first_path + "/messages",
                    json={
                        "content": (
                            "Continue the confirmed proposal. I explicitly confirm its remaining "
                            "write: call create_subtask once with task_id task-0001 and title "
                            "Validate API. Return the exact Tool result record."
                        )
                    },
                )
                assert confirmed_child.status_code == 201, confirmed_child.text
                child_assistant = confirmed_child.json()["messages"][-1]["content"]
                assert "task-0002" in child_assistant

                parent = task_client.get("/tasks/task-0001")
                child = task_client.get("/tasks/task-0002")
                assert parent.status_code == child.status_code == 200
                assert parent.json()["title"] == "Release Alpha"
                assert parent.json()["subtask_ids"] == ["task-0002"]
                assert child.json()["title"] == "Validate API"
                assert child.json()["parent_id"] == "task-0001"

                first_filters: dict[str, str | list[str] | bool] = {
                    "label": f"io.universal-agent-runtime.agent={agents[0]}"
                }
                second_filters: dict[str, str | list[str] | bool] = {
                    "label": f"io.universal-agent-runtime.agent={agents[1]}"
                }
                first_container = docker_client.containers.list(
                    all=True, filters=first_filters
                )
                second_container = docker_client.containers.list(
                    all=True, filters=second_filters
                )
                assert len(first_container) == len(second_container) == 1
                assert first_container[0].id != second_container[0].id

                assert client.post(first_path + "/stop").status_code == 200
                assert client.post(first_path + "/start").status_code == 200
                continuity = client.post(
                    first_path + "/messages",
                    json={
                        "content": "What isolation marker did I ask you to remember? Reply only it."
                    },
                )
                assert continuity.status_code == 201, continuity.text
                assert isolation_marker in continuity.json()["messages"][-1]["content"]

                second_path = f"/agents/{agents[1]}"
                isolated = client.post(
                    second_path + "/messages",
                    json={
                        "content": "Reply only UNKNOWN because no marker was given in this Session."
                    },
                )
                assert isolated.status_code == 201, isolated.text
                assert (
                    isolation_marker not in isolated.json()["messages"][-1]["content"]
                )
                assert (
                    client.get(second_path + "/messages")
                    .json()["messages"][0]["content"]
                    .endswith("this Session.")
                )
            finally:
                for agent_id in agents:
                    _cleanup_agent(client, agent_id)

        for agent_id in agents:
            filters: dict[str, str | list[str] | bool] = {
                "label": f"io.universal-agent-runtime.agent={agent_id}"
            }
            assert not docker_client.containers.list(all=True, filters=filters)
            assert not docker_client.volumes.list(filters=filters)
            assert not (settings.qwen_storage_root / agent_id).exists()
    finally:
        docker_client.close()
        orchestrator_server.stop()
        task_server.stop()
