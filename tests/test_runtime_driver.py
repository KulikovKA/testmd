from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    ExecutionState,
    OperationOptions,
    Readiness,
    ResourceLimits,
)
from universal_agent_runtime.composition import _compose_interaction, _compose_runtime
from universal_agent_runtime.configuration import (
    ApplicationSettings,
    ConfigurationError,
    RuntimeDriver,
)
from universal_agent_runtime.domain.identifiers import AgentId, WorkspaceId


class _Container:
    def __init__(self, collection: _Containers, name: str) -> None:
        self._collection = collection
        self._name = name
        self.attrs: dict[str, Any] = {"State": {"Status": "created", "ExitCode": 0}}

    def reload(self) -> None:
        return None

    def start(self) -> None:
        self.attrs = {
            "State": {
                "Status": "running",
                "ExitCode": 0,
                "Health": {"Status": "healthy"},
            }
        }

    def stop(self, *, timeout: int) -> None:
        assert timeout == 1
        self.attrs = {"State": {"Status": "exited", "ExitCode": 0}}

    def remove(self, *, force: bool, v: bool) -> None:
        assert force is True and v is False
        self._collection.items.pop(self._name)


class _Containers:
    def __init__(self) -> None:
        self.items: dict[str, _Container] = {}
        self.create_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def create(self, *args: Any, **kwargs: Any) -> _Container:
        self.create_calls.append((args, kwargs))
        container = _Container(self, kwargs["name"])
        self.items[kwargs["name"]] = container
        return container

    def get(self, name: str) -> _Container:
        return self.items[name]


class _Volume:
    def __init__(self, collection: _Volumes, name: str) -> None:
        self._collection = collection
        self._name = name

    def remove(self, *, force: bool) -> None:
        assert force is True
        self._collection.items.pop(self._name)


class _Volumes:
    def __init__(self) -> None:
        self.items: dict[str, _Volume] = {}

    def create(self, *, name: str, labels: dict[str, str]) -> _Volume:
        assert labels
        volume = _Volume(self, name)
        self.items[name] = volume
        return volume

    def get(self, name: str) -> _Volume:
        return self.items[name]


class _DockerClient:
    def __init__(self) -> None:
        self.containers = _Containers()
        self.volumes = _Volumes()

    def close(self) -> None:
        return None


def _environment(driver: str) -> dict[str, str]:
    return {
        "UAR_API_HOST": "127.0.0.1",
        "UAR_API_PORT": "8080",
        "UAR_RUNTIME_DRIVER": driver,
        "UAR_DOCKER_WORKLOAD_KEY": "qwen-agent-image",
        "UAR_DOCKER_WORKLOAD_IMAGE": "uar-agent:0.1.0",
        "UAR_DOCKER_WORKLOAD_COMMAND_JSON": '["serve"]',
        "UAR_DOCKER_WORKLOAD_USER": "10001:10001",
        "UAR_DOCKER_WORKSPACE_TARGET": "/workspace",
        "UAR_DOCKER_NETWORK_MODE": "bridge",
        "UAR_DOCKER_NETWORK_HOST": "10.21.171.2",
        "UAR_DOCKER_NETWORK_PORT": "11434",
        "UAR_DOCKER_HEALTHCHECK_INTERVAL_SECONDS": "1",
        "UAR_DOCKER_HEALTHCHECK_TIMEOUT_SECONDS": "2",
        "UAR_DOCKER_HEALTHCHECK_RETRIES": "30",
        "UAR_DOCKER_HEALTHCHECK_START_PERIOD_SECONDS": "1",
        "UAR_AGENT_CPU_CORES": "1",
        "UAR_AGENT_MEMORY_BYTES": "1073741824",
        "UAR_AGENT_OPERATION_TIMEOUT_SECONDS": "30",
        "UAR_AGENT_READINESS_TIMEOUT_SECONDS": "30",
        "UAR_AGENT_READINESS_POLL_INTERVAL_SECONDS": "0.1",
        "UAR_QWEN_SESSION_STORAGE_ROOT": ".runtime/test-sessions",
        "UAR_QWEN_BASE_URL": "http://10.21.171.2:11434/v1",
        "UAR_QWEN_MODEL": "qwen-3.8-multimodal:latest",
        "UAR_QWEN_REASONING_DIRECTIVE": "/no_think",
        "UAR_QWEN_API_KEY_SECRET_ID": "ollama-api-key",
        "UAR_QWEN_API_KEY": "test-placeholder",
    }


class RuntimeDriverConfigurationTests(unittest.TestCase):
    def test_docker_composition_does_not_force_container_runtime(self) -> None:
        settings = ApplicationSettings.from_environment(_environment("docker"))
        with patch(
            "universal_agent_runtime.adapters.docker_runtime.docker.from_env",
            return_value=_DockerClient(),
        ):
            runtime = _compose_runtime(settings)

        self.assertIsInstance(runtime, DockerRuntime)
        self.assertIsNone(runtime._workloads["qwen-agent-image"].container_runtime)

    def test_kata_composition_selects_kata_container_runtime(self) -> None:
        settings = ApplicationSettings.from_environment(_environment("kata"))
        with patch(
            "universal_agent_runtime.adapters.docker_runtime.docker.from_env",
            return_value=_DockerClient(),
        ):
            runtime = _compose_runtime(settings)

        self.assertIsInstance(runtime, DockerRuntime)
        self.assertEqual(settings.runtime_driver, RuntimeDriver.KATA)
        self.assertEqual(
            runtime._workloads["qwen-agent-image"].container_runtime, "kata"
        )

    def test_unknown_runtime_driver_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ConfigurationError, "UAR_RUNTIME_DRIVER must be docker or kata"
        ):
            ApplicationSettings.from_environment(_environment("unknown"))

    def test_sfera_credentials_are_required_as_a_pair_and_redacted_from_repr(self) -> None:
        settings = ApplicationSettings.from_environment(
            {
                **_environment("kata"),
                "UAR_SFERA_BASE_URL": "https://sfera.ai.dev.sfera-t1.ru",
                "UAR_SFERA_USERNAME_SECRET_ID": "sfera-username",
                "UAR_SFERA_USERNAME": "private-user",
                "UAR_SFERA_PASSWORD_SECRET_ID": "sfera-password",
                "UAR_SFERA_PASSWORD": "private-password",
                "UAR_SFERA_DEFAULT_OWNER": "sfera-admin",
            }
        )
        self.assertEqual(settings.sfera_base_url, "https://sfera.ai.dev.sfera-t1.ru")
        self.assertEqual(settings.sfera_default_owner, "sfera-admin")
        self.assertNotIn("private-user", repr(settings))
        self.assertNotIn("private-password", repr(settings))
        with self.assertRaisesRegex(ConfigurationError, "Sfera"):
            ApplicationSettings.from_environment(
                {
                    **_environment("docker"),
                    "UAR_SFERA_BASE_URL": "https://sfera.ai.dev.sfera-t1.ru",
                    "UAR_SFERA_USERNAME_SECRET_ID": "sfera-username",
                }
            )

        with self.assertRaisesRegex(ConfigurationError, "UAR_SFERA_DEFAULT_OWNER"):
            ApplicationSettings.from_environment(
                {
                    **_environment("docker"),
                    "UAR_SFERA_DEFAULT_OWNER": "invalid owner",
                }
            )

    def test_sfera_ca_certificate_must_be_an_absolute_readable_pem(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            certificate = Path(directory) / "sfera-ca.pem"
            certificate.write_text(
                "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
                encoding="ascii",
            )
            settings = ApplicationSettings.from_environment(
                {
                    **_environment("kata"),
                    "UAR_SFERA_BASE_URL": "https://sfera.ai.dev.sfera-t1.ru",
                    "UAR_SFERA_USERNAME_SECRET_ID": "sfera-username",
                    "UAR_SFERA_USERNAME": "private-user",
                    "UAR_SFERA_PASSWORD_SECRET_ID": "sfera-password",
                    "UAR_SFERA_PASSWORD": "private-password",
                    "UAR_SFERA_CA_CERT_PATH": str(certificate),
                }
            )
            self.assertEqual(settings.sfera_ca_cert_path, certificate.resolve())
            with patch(
                "universal_agent_runtime.adapters.qwen_session.docker.from_env",
                return_value=object(),
            ):
                interaction = _compose_interaction(settings)
            self.assertEqual(interaction._config.sfera_ca_cert_path, certificate.resolve())

        with self.assertRaisesRegex(ConfigurationError, "absolute path"):
            ApplicationSettings.from_environment(
                {
                    **_environment("docker"),
                    "UAR_SFERA_CA_CERT_PATH": "sfera-ca.pem",
                }
            )
        with self.assertRaisesRegex(ConfigurationError, "UAR_SFERA_BASE_URL"):
            ApplicationSettings.from_environment(
                {
                    **_environment("docker"),
                    "UAR_SFERA_BASE_URL": "https://sfera.ai.dev.sfera-t1.ru/untrusted",
                }
            )


class DockerRuntimeSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def _exercise_lifecycle(
        self, container_runtime: str | None
    ) -> tuple[list[tuple[tuple[Any, ...], dict[str, Any]]], list[ExecutionState]]:
        client = _DockerClient()
        runtime = DockerRuntime(
            {
                "qwen-agent-image": DockerWorkload(
                    "uar-agent:0.1.0",
                    ("serve",),
                    "10001:10001",
                    container_runtime=container_runtime,
                )
            },
            client=client,
        )
        request = CreateRuntimeRequest(
            AgentId("agent-one"),
            WorkspaceId("workspace-one"),
            "qwen-agent-image",
            ResourceLimits(1.0, 1_073_741_824),
        )
        options = OperationOptions(2)

        created = await runtime.create(request, options=options)
        started = await runtime.start(created.handle, options=options)
        observed = await runtime.status(created.handle, options=options)
        stopped = await runtime.stop(created.handle, options=options)
        await runtime.delete(created.handle, options=options)

        self.assertEqual(started.readiness, Readiness.CONFIRMED)
        self.assertEqual(observed.readiness, Readiness.CONFIRMED)
        self.assertFalse(client.containers.items)
        self.assertFalse(client.volumes.items)
        return client.containers.create_calls, [
            created.execution,
            started.execution,
            observed.execution,
            stopped.execution,
        ]

    async def test_docker_and_kata_share_lifecycle_semantics(self) -> None:
        docker_calls, docker_states = await self._exercise_lifecycle(None)
        kata_calls, kata_states = await self._exercise_lifecycle("kata")

        self.assertEqual(docker_states, kata_states)
        self.assertEqual(
            docker_states,
            [
                ExecutionState.INACTIVE,
                ExecutionState.EXECUTING,
                ExecutionState.EXECUTING,
                ExecutionState.INACTIVE,
            ],
        )
        self.assertEqual(len(docker_calls), 1)
        self.assertEqual(len(kata_calls), 1)
        self.assertNotIn("runtime", docker_calls[0][1])
        self.assertEqual(kata_calls[0][1]["runtime"], "kata")


if __name__ == "__main__":
    unittest.main()
