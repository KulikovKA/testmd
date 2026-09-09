"""HTTP contract tests for TASK-009 Agent lifecycle operations."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.runtime_support.fake_interaction import FakeInteraction
from tests.runtime_support.fake_runtime import FakeRuntime
from universal_agent_runtime.application.ports.runtime_values import (
    OperationOptions,
    RuntimeHandle,
    RuntimeObservation,
)
from universal_agent_runtime.composition import compose_application
from universal_agent_runtime.configuration import ApplicationSettings
from universal_agent_runtime.http_api import create_application


class _ReadyRuntime(FakeRuntime):
    async def start(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        await super().start(handle, options=options)
        await self.confirm_ready(handle)
        return await self.status(handle, options=options)


def _environment(root: Path) -> dict[str, str]:
    return {
        "UAR_API_HOST": "127.0.0.1",
        "UAR_API_PORT": "8080",
        "UAR_RUNTIME_DRIVER": "docker",
        "UAR_DOCKER_WORKLOAD_KEY": "test-workload",
        "UAR_DOCKER_WORKLOAD_IMAGE": "unused-by-fake",
        "UAR_DOCKER_WORKLOAD_COMMAND_JSON": '["serve"]',
        "UAR_DOCKER_WORKLOAD_USER": "10001:10001",
        "UAR_DOCKER_WORKSPACE_TARGET": "/workspace",
        "UAR_DOCKER_NETWORK_MODE": "none",
        "UAR_DOCKER_HEALTHCHECK_INTERVAL_SECONDS": "1",
        "UAR_DOCKER_HEALTHCHECK_TIMEOUT_SECONDS": "2",
        "UAR_DOCKER_HEALTHCHECK_RETRIES": "30",
        "UAR_DOCKER_HEALTHCHECK_START_PERIOD_SECONDS": "1",
        "UAR_AGENT_CPU_CORES": "1.0",
        "UAR_AGENT_MEMORY_BYTES": "67108864",
        "UAR_AGENT_OPERATION_TIMEOUT_SECONDS": "1",
        "UAR_AGENT_READINESS_TIMEOUT_SECONDS": "0.02",
        "UAR_AGENT_READINESS_POLL_INTERVAL_SECONDS": "0.001",
        "UAR_QWEN_SESSION_STORAGE_ROOT": str(root / "sessions"),
        "UAR_QWEN_BASE_URL": "http://local.invalid/v1",
        "UAR_QWEN_MODEL": "test-model",
        "UAR_QWEN_API_KEY_SECRET_ID": "safe-test-key",
        "UAR_QWEN_API_KEY": "do-not-expose-this-placeholder",
    }


def _app(
    root: Path, runtime: FakeRuntime | None = None
) -> tuple[object, FakeRuntime, FakeInteraction]:
    selected_runtime = runtime or _ReadyRuntime()
    interaction = FakeInteraction()
    settings = ApplicationSettings.from_environment(_environment(root))
    composition = compose_application(
        settings, runtime=selected_runtime, interaction=interaction
    )
    return create_application(composition), selected_runtime, interaction


def test_full_http_lifecycle_is_explicit_idempotent_and_session_safe(
    tmp_path: Path,
) -> None:
    app, _, interaction = _app(tmp_path)
    payload = {
        "request_id": "request-one",
        "skills": ["skill-a"],
        "tools": ["tool-a"],
    }

    with TestClient(app) as client:  # type: ignore[arg-type]
        created = client.post("/agents", json=payload)
        assert created.status_code == 201
        body = created.json()
        agent_id = body["agent_id"]
        assert body["state"] == "STOPPED"
        assert body["workspace_id"].startswith("workspace-")
        assert body["session_id"].startswith("session-")
        assert body["configuration"] == {
            "workload": "test-workload",
            "cpu_cores": 1.0,
            "memory_bytes": 67108864,
            "skills": ["skill-a"],
            "tools": ["tool-a"],
        }
        assert "runtime_handle" not in body

        replay = client.post("/agents", json=payload)
        assert replay.status_code == 200
        assert replay.json()["agent_id"] == agent_id

        assert client.get(f"/agents/{agent_id}").json() == body
        started = client.post(f"/agents/{agent_id}/start")
        assert started.status_code == 200
        assert started.json()["state"] == "READY"
        assert started.json()["runtime"] == {
            "execution": "executing",
            "readiness": "confirmed",
        }

        invalid_delete = client.delete(f"/agents/{agent_id}")
        assert invalid_delete.status_code == 409
        assert invalid_delete.json()["error"] == {
            "code": "invalid_state",
            "message": "Agent state does not allow the operation",
            "operation": "delete",
            "agent_id": agent_id,
            "state": "READY",
            "retryable": False,
        }

        stopped = client.post(f"/agents/{agent_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "STOPPED"
        assert interaction.sessions

        assert client.delete(f"/agents/{agent_id}").status_code == 204
        assert not interaction.sessions
        assert client.delete(f"/agents/{agent_id}").status_code == 204
        assert client.get(f"/agents/{agent_id}").status_code == 404


def test_openapi_contains_lifecycle_and_chat_but_no_event_or_task_routes(
    tmp_path: Path,
) -> None:
    app, _, _ = _app(tmp_path)

    with TestClient(app) as client:  # type: ignore[arg-type]
        schema = client.get("/openapi.json").json()

    assert set(schema["paths"]) == {
        "/healthz",
        "/readyz",
        "/agents",
        "/agents/{agent_id}",
        "/agents/{agent_id}/start",
        "/agents/{agent_id}/stop",
        "/agents/{agent_id}/messages",
        "/agents/{agent_id}/messages/stream",
    }
    paths = " ".join(schema["paths"])
    assert "messages" in paths
    assert "events" not in paths
    assert "tasks" not in paths


def test_readiness_timeout_returns_redacted_recoverable_failure(
    tmp_path: Path,
) -> None:
    app, _, _ = _app(tmp_path, FakeRuntime())

    with TestClient(app) as client:  # type: ignore[arg-type]
        created = client.post("/agents", json={"request_id": "timeout-case"})
        agent_id = created.json()["agent_id"]
        failed = client.post(f"/agents/{agent_id}/start")
        inspected = client.get(f"/agents/{agent_id}")

    assert failed.status_code == 504
    assert failed.json()["error"] == {
        "code": "readiness_timeout",
        "message": "Agent readiness timed out",
        "operation": "start",
        "agent_id": agent_id,
        "state": "FAILED",
        "retryable": True,
    }
    assert inspected.json()["state"] == "FAILED"
    assert "do-not-expose-this-placeholder" not in failed.text
    assert "local.invalid" not in failed.text


def test_transport_validation_and_creation_conflict_are_deterministic(
    tmp_path: Path,
) -> None:
    app, _, _ = _app(tmp_path)

    with TestClient(app) as client:  # type: ignore[arg-type]
        invalid = client.post(
            "/agents", json={"request_id": "bad id", "unknown": "rejected"}
        )
        first = client.post(
            "/agents", json={"request_id": "same-key", "skills": ["one"]}
        )
        conflict = client.post(
            "/agents", json={"request_id": "same-key", "skills": ["two"]}
        )

    assert invalid.status_code == 422
    assert invalid.json() == {
        "error": {
            "code": "request_invalid",
            "message": "Request validation failed",
        }
    }
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "conflict"
