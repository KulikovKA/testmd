"""Real Docker validation of the TASK-009 public lifecycle API."""

from pathlib import Path
from uuid import uuid4

import docker
import pytest
from fastapi.testclient import TestClient

from tests.runtime_support.docker_runtime import docker_available
from tests.runtime_support.fake_interaction import FakeInteraction
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application

_IMAGE = "uar-task007-agent:local"

pytestmark = pytest.mark.skipif(
    not docker_available(), reason="local Docker daemon is unavailable"
)


def _build_agent_image() -> None:
    client = docker.from_env()
    try:
        root = Path(__file__).parents[2] / "agent_image"
        client.images.build(path=str(root), tag=_IMAGE, rm=True, pull=False)
    finally:
        client.close()


def _environment(root: Path) -> dict[str, str]:
    return {
        "UAR_API_HOST": "127.0.0.1",
        "UAR_API_PORT": "8080",
        "UAR_RUNTIME_DRIVER": "docker",
        "UAR_DOCKER_WORKLOAD_KEY": "qwen-agent-image",
        "UAR_DOCKER_WORKLOAD_IMAGE": _IMAGE,
        "UAR_DOCKER_WORKLOAD_COMMAND_JSON": '["serve"]',
        "UAR_DOCKER_WORKLOAD_USER": "10001:10001",
        "UAR_DOCKER_WORKSPACE_TARGET": "/workspace",
        "UAR_DOCKER_NETWORK_MODE": "none",
        "UAR_DOCKER_HEALTHCHECK_INTERVAL_SECONDS": "0.2",
        "UAR_DOCKER_HEALTHCHECK_TIMEOUT_SECONDS": "1",
        "UAR_DOCKER_HEALTHCHECK_RETRIES": "30",
        "UAR_DOCKER_HEALTHCHECK_START_PERIOD_SECONDS": "0.2",
        "UAR_AGENT_CPU_CORES": "1.0",
        "UAR_AGENT_MEMORY_BYTES": "536870912",
        "UAR_AGENT_OPERATION_TIMEOUT_SECONDS": "30",
        "UAR_AGENT_READINESS_TIMEOUT_SECONDS": "15",
        "UAR_AGENT_READINESS_POLL_INTERVAL_SECONDS": "0.1",
        "UAR_QWEN_SESSION_STORAGE_ROOT": str(root / "sessions"),
        "UAR_QWEN_BASE_URL": "http://host.docker.internal:11434/v1",
        "UAR_QWEN_MODEL": "qwen3:1.7b",
        "UAR_QWEN_API_KEY_SECRET_ID": "local-test-key",
        "UAR_QWEN_API_KEY": "ollama",
    }


def test_public_lifecycle_runs_universal_agent_image_and_cleans_resources(
    tmp_path: Path,
) -> None:
    _build_agent_image()
    settings = ApplicationSettings.from_environment(_environment(tmp_path))
    composition = compose_application(settings, interaction=FakeInteraction())
    app = create_application(composition)
    agent_id: str | None = None
    created_agent_id: str | None = None

    with TestClient(app) as client:
        try:
            created = client.post(
                "/agents", json={"request_id": f"docker-{uuid4().hex[:12]}"}
            )
            assert created.status_code == 201, created.text
            agent_id = created.json()["agent_id"]
            created_agent_id = agent_id
            assert created.json()["state"] == "STOPPED"

            started = client.post(f"/agents/{agent_id}/start")
            assert started.status_code == 200, started.text
            assert started.json()["state"] == "READY"
            assert started.json()["runtime"] == {
                "execution": "executing",
                "readiness": "confirmed",
            }

            stopped = client.post(f"/agents/{agent_id}/stop")
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["state"] == "STOPPED"

            restarted = client.post(f"/agents/{agent_id}/start")
            assert restarted.status_code == 200, restarted.text
            assert restarted.json()["state"] == "READY"
            assert client.post(f"/agents/{agent_id}/stop").status_code == 200
            assert client.delete(f"/agents/{agent_id}").status_code == 204
            agent_id = None
        finally:
            if agent_id is not None:
                client.post(f"/agents/{agent_id}/stop")
                client.delete(f"/agents/{agent_id}")

    docker_client = docker.from_env()
    try:
        assert created_agent_id is not None
        managed_containers = docker_client.containers.list(
            all=True,
            filters={"label": f"io.universal-agent-runtime.agent={created_agent_id}"},
        )
        managed_volumes = docker_client.volumes.list(
            filters={"label": f"io.universal-agent-runtime.agent={created_agent_id}"}
        )
        assert not managed_containers
        assert not managed_volumes
    finally:
        docker_client.close()
