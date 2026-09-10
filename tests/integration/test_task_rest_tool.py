"""Opt-in Qwen Code MCP invocation against the bounded mock Task service."""

import os
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import docker
import pytest
import uvicorn
from fastapi.testclient import TestClient

from mock_task_service.api import create_app
from mock_task_service.store import InMemoryTaskStore
from tests.integration.test_agent_lifecycle_api import _build_agent_image, _environment
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application


@pytest.mark.skipif(
    os.getenv("RUN_QWEN_OLLAMA_INTEGRATION") != "1",
    reason="requires explicitly configured Docker/Ollama services",
)
def test_qwen_skill_creates_explicitly_confirmed_task_through_restricted_task_mcp(
    tmp_path: Path,
) -> None:
    _build_agent_image()
    listener = socket.socket()
    listener.bind(("0.0.0.0", 0))
    listener.listen()
    task_port = listener.getsockname()[1]
    store = InMemoryTaskStore()
    task_server = uvicorn.Server(
        uvicorn.Config(create_app(store), log_level="error", access_log=False)
    )
    task_thread = threading.Thread(
        target=task_server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    task_thread.start()
    deadline = time.monotonic() + 10
    while (
        not task_server.started
        and task_thread.is_alive()
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    assert task_server.started

    environment = _environment(tmp_path)
    endpoint = os.getenv("QWEN_OLLAMA_BASE_URL", environment["UAR_QWEN_BASE_URL"])
    parsed = urlparse(endpoint)
    environment.update(
        {
            "UAR_DOCKER_NETWORK_MODE": "bridge",
            "UAR_DOCKER_NETWORK_HOST": str(parsed.hostname),
            "UAR_DOCKER_NETWORK_PORT": str(
                parsed.port or (443 if parsed.scheme == "https" else 80)
            ),
            "UAR_QWEN_BASE_URL": endpoint,
            "UAR_QWEN_MODEL": os.getenv("QWEN_OLLAMA_MODEL", "qwen3:0.6b"),
            "UAR_QWEN_API_KEY": os.getenv("QWEN_OLLAMA_API_KEY", "ollama"),
            "UAR_TASK_API_BASE_URL": (f"http://host.docker.internal:{task_port}"),
            "UAR_TASK_API_TIMEOUT_SECONDS": "10",
        }
    )
    settings = ApplicationSettings.from_environment(environment)
    app = create_application(compose_application(settings))
    docker_client = docker.from_env()
    agent_id: str | None = None
    try:
        with TestClient(app) as client:
            created = client.post(
                "/agents",
                json={
                    "request_id": uuid4().hex,
                    "skills": ["task-decomposition"],
                    "tools": ["create_task"],
                },
            )
            assert created.status_code == 201, created.text
            agent_id = created.json()["agent_id"]
            path = f"/agents/{agent_id}"
            assert client.post(path + "/start").status_code == 200
            response = client.post(
                path + "/messages",
                json={
                    "content": (
                        "The proposed decomposition is exactly one Task titled Parent, with no "
                        "subtasks. I explicitly confirm that proposal now. Call create_task once "
                        "and reply with the created record."
                    )
                },
            )
            assert response.status_code == 201, response.text
            parent = store.get("task-0001")
            assert parent.title == "Parent"
            assert client.post(path + "/stop").status_code == 200
            assert client.delete(path).status_code == 204
    finally:
        if agent_id is not None:
            filters: dict[str, str | list[str] | bool] = {
                "label": f"io.universal-agent-runtime.agent={agent_id}"
            }
            for container in docker_client.containers.list(all=True, filters=filters):
                container.remove(force=True)
            for volume in docker_client.volumes.list(filters=filters):
                volume.remove(force=True)
        docker_client.close()
        task_server.should_exit = True
        task_thread.join(timeout=10)
