"""Tests for the intentionally small TASK-008 HTTP application surface."""

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from universal_agent_runtime.application.ports.interaction_values import (
    DeleteSessionResult,
    SessionObservation,
    SessionReference,
    TurnRequest,
    TurnResult,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    DeleteResult,
    OperationOptions,
    RuntimeHandle,
    RuntimeObservation,
)
from universal_agent_runtime.composition import (
    ApplicationComposition,
    compose_application,
)
from universal_agent_runtime.configuration import (
    ApplicationSettings,
    ConfigurationError,
    RuntimeDriver,
)
from universal_agent_runtime.http_api import (
    create_application,
    create_application_from_environment,
)


class _FakeRuntime:
    def __init__(self) -> None:
        self.closed = False

    async def create(
        self, _: CreateRuntimeRequest, *, options: OperationOptions
    ) -> RuntimeObservation:
        raise AssertionError("lifecycle endpoints are not part of TASK-008")

    async def start(
        self, _: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        raise AssertionError("lifecycle endpoints are not part of TASK-008")

    async def status(
        self, _: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        raise AssertionError("lifecycle endpoints are not part of TASK-008")

    async def stop(
        self, _: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        raise AssertionError("lifecycle endpoints are not part of TASK-008")

    async def delete(
        self, _: RuntimeHandle, *, options: OperationOptions
    ) -> DeleteResult:
        raise AssertionError("lifecycle endpoints are not part of TASK-008")

    async def close(self) -> None:
        self.closed = True


class _FakeInteraction:
    def __init__(self) -> None:
        self.closed = False

    async def create_session(self, _: SessionReference) -> SessionObservation:
        raise AssertionError("chat endpoints are not part of TASK-008")

    async def turn(self, _: TurnRequest) -> TurnResult:
        raise AssertionError("chat endpoints are not part of TASK-008")

    async def delete_session(self, _: SessionReference) -> DeleteSessionResult:
        raise AssertionError("chat endpoints are not part of TASK-008")

    async def close(self) -> None:
        self.closed = True


def _environment(root: Path) -> dict[str, str]:
    return {
        "UAR_API_HOST": "127.0.0.1",
        "UAR_API_PORT": "8080",
        "UAR_RUNTIME_DRIVER": "docker",
        "UAR_DOCKER_WORKLOAD_KEY": "qwen-agent-image",
        "UAR_DOCKER_WORKLOAD_IMAGE": "uar-task007-agent:local",
        "UAR_DOCKER_WORKLOAD_COMMAND_JSON": '["serve"]',
        "UAR_DOCKER_WORKLOAD_USER": "10001:10001",
        "UAR_DOCKER_WORKSPACE_TARGET": "/workspace",
        "UAR_DOCKER_NETWORK_MODE": "none",
        "UAR_DOCKER_HEALTHCHECK_INTERVAL_SECONDS": "1",
        "UAR_DOCKER_HEALTHCHECK_TIMEOUT_SECONDS": "2",
        "UAR_DOCKER_HEALTHCHECK_RETRIES": "30",
        "UAR_DOCKER_HEALTHCHECK_START_PERIOD_SECONDS": "1",
        "UAR_AGENT_CPU_CORES": "1.0",
        "UAR_AGENT_MEMORY_BYTES": "1073741824",
        "UAR_AGENT_OPERATION_TIMEOUT_SECONDS": "30",
        "UAR_AGENT_READINESS_TIMEOUT_SECONDS": "30",
        "UAR_AGENT_READINESS_POLL_INTERVAL_SECONDS": "0.1",
        "UAR_QWEN_SESSION_STORAGE_ROOT": str(root / "sessions"),
        "UAR_QWEN_BASE_URL": "http://host.docker.internal:11434/v1",
        "UAR_QWEN_MODEL": "qwen3:1.7b",
        "UAR_QWEN_API_KEY_SECRET_ID": "safe-test-key",
        "UAR_QWEN_API_KEY": "safe-test-placeholder",
    }


def _app(root: Path) -> tuple[FastAPI, _FakeRuntime, _FakeInteraction]:
    settings = ApplicationSettings.from_environment(_environment(root))
    runtime = _FakeRuntime()
    interaction = _FakeInteraction()
    composition = compose_application(
        settings, runtime=runtime, interaction=interaction
    )
    return create_application(composition), runtime, interaction


def test_health_readiness_openapi_and_docs_preserve_foundation_routes(
    tmp_path: Path,
) -> None:
    app, runtime, interaction = _app(tmp_path)

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {
            "status": "ready",
            "runtime_driver": "docker",
        }
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
        assert {"/healthz", "/readyz"}.issubset(schema["paths"])
        assert "/agents" in schema["paths"]
        assert "/agents/{agent_id}/messages" in schema["paths"]
        assert "/agents/{agent_id}/events" not in schema["paths"]

    assert runtime.closed is True
    assert interaction.closed is True


def test_not_found_uses_the_redacted_common_error_envelope(tmp_path: Path) -> None:
    app, _, _ = _app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/not-a-route")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Resource not found"}
    }
    assert "not-a-route" not in response.text


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("UAR_RUNTIME_DRIVER", "kata", "UAR_RUNTIME_DRIVER must be docker"),
        (
            "UAR_DOCKER_WORKLOAD_COMMAND_JSON",
            "not-json",
            "UAR_DOCKER_WORKLOAD_COMMAND_JSON",
        ),
        ("UAR_DOCKER_NETWORK_MODE", "host", "UAR_DOCKER_NETWORK_MODE"),
        ("UAR_AGENT_CPU_CORES", "0", "UAR_AGENT_CPU_CORES"),
        ("UAR_DOCKER_HEALTHCHECK_RETRIES", "0", "UAR_DOCKER_HEALTHCHECK_RETRIES"),
    ],
)
def test_configuration_is_validated_before_composition(
    tmp_path: Path, name: str, value: str, message: str
) -> None:
    environment = _environment(tmp_path)
    environment[name] = value

    with pytest.raises(ConfigurationError, match=message):
        ApplicationSettings.from_environment(environment)


def test_configuration_requires_all_mandatory_values(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    del environment["UAR_QWEN_API_KEY"]

    with pytest.raises(ConfigurationError, match="UAR_QWEN_API_KEY"):
        ApplicationSettings.from_environment(environment)


def test_composition_selects_docker_at_root_with_injected_interaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = ApplicationSettings.from_environment(_environment(tmp_path))
    interaction = _FakeInteraction()
    runtime = _FakeRuntime()

    def fake_runtime(selected: ApplicationSettings) -> _FakeRuntime:
        assert selected.runtime_driver is RuntimeDriver.DOCKER
        return runtime

    monkeypatch.setattr(
        "universal_agent_runtime.composition._compose_runtime", fake_runtime
    )
    composition = compose_application(settings, interaction=interaction)

    assert composition.settings is settings
    assert composition.settings.runtime_driver is RuntimeDriver.DOCKER
    assert composition.runtime is runtime
    assert composition.interaction is interaction
    assert composition.lifecycle is not None
    asyncio.run(composition.close())
    assert interaction.closed is True


def test_environment_factory_uses_validated_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = compose_application(
        ApplicationSettings.from_environment(_environment(tmp_path)),
        runtime=_FakeRuntime(),
        interaction=_FakeInteraction(),
    )

    def fake_compose(settings: ApplicationSettings) -> ApplicationComposition:
        assert settings.runtime_driver is RuntimeDriver.DOCKER
        return expected

    monkeypatch.setattr(
        "universal_agent_runtime.http_api.compose_application", fake_compose
    )
    app = create_application_from_environment(_environment(tmp_path))

    with TestClient(app) as client:
        assert client.get("/readyz").status_code == 200
