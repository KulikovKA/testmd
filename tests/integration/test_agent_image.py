"""Opt-in end-to-end validation of the reusable Qwen Code agent image."""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import docker
import pytest

from tests.runtime_support.docker_runtime import docker_available
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.application.ports.runtime_errors import RuntimeFailure
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    EnvironmentVariable,
    ExecutionState,
    NetworkDestination,
    OperationOptions,
    Readiness,
    ResourceLimits,
    SecretBinding,
)
from universal_agent_runtime.domain.identifiers import AgentId, WorkspaceId

_IMAGE = "uar-task007-agent:local"
_QWEN_IMAGE = (
    "ghcr.io/qwenlm/qwen-code@"
    "sha256:996a12729e25f694254768ac8d3b5f870c54e6ac5825e169299e85a19c78cc10"
)

pytestmark = [
    pytest.mark.skipif(
        not docker_available(), reason="local Docker daemon is unavailable"
    ),
    pytest.mark.skipif(
        os.getenv("RUN_QWEN_OLLAMA_INTEGRATION") != "1",
        reason="requires the explicitly configured local Docker and Ollama services",
    ),
]


def _build_agent_image() -> None:
    client = docker.from_env()
    try:
        root = Path(__file__).parents[2] / "agent_image"
        client.images.build(path=str(root), tag=_IMAGE, rm=True, pull=False)
    finally:
        client.close()


def _exec_output(output: bytes | Iterator[bytes]) -> str:
    raw = output if isinstance(output, bytes) else b"".join(output)
    return raw.decode("utf-8", errors="replace")


def test_agent_image_runs_qwen_session_through_docker_runtime() -> None:
    _build_agent_image()
    prefix = f"uar-agent-image-{uuid4().hex[:12]}"
    endpoint = os.getenv("QWEN_OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1")
    model = os.getenv("QWEN_OLLAMA_MODEL", "qwen3:1.7b")
    api_key = os.getenv("QWEN_OLLAMA_API_KEY", "ollama")
    destination = NetworkDestination("host.docker.internal", 11434)
    healthcheck = {
        "test": ["CMD-SHELL", "agent-runtime readiness"],
        "interval": 100_000_000,
        "timeout": 100_000_000,
        "retries": 10,
        "start_period": 100_000_000,
    }
    driver = DockerRuntime(
        {
            "qwen-agent-image": DockerWorkload(
                _IMAGE,
                ("serve",),
                "10001:10001",
                healthcheck=healthcheck,
                network_mode="bridge",
                network_destinations=(destination,),
            )
        },
        secret_resolver=lambda _: api_key,
        resource_prefix=prefix,
    )
    request = CreateRuntimeRequest(
        AgentId(f"image-agent-{uuid4().hex[:8]}"),
        WorkspaceId(f"image-workspace-{uuid4().hex[:8]}"),
        "qwen-agent-image",
        ResourceLimits(1.0, 1024 * 1024 * 1024),
        environment=(
            EnvironmentVariable("QWEN_OLLAMA_BASE_URL", endpoint),
            EnvironmentVariable("QWEN_OLLAMA_MODEL", model),
            EnvironmentVariable("QWEN_AGENT_MAX_WALL_TIME_SECONDS", "300"),
        ),
        secrets=(SecretBinding("OPENAI_API_KEY", "local-ollama-key"),),
        network=(destination,),
    )
    options = OperationOptions(30)

    async def scenario() -> None:
        client = docker.from_env()
        handle = None
        try:
            created = await driver.create(request, options=options)
            handle = created.handle
            started = await driver.start(handle, options=options)
            assert started.execution is ExecutionState.EXECUTING

            container = (
                await asyncio.to_thread(
                    client.containers.list,
                    all=True,
                    filters={
                        "label": f"io.universal-agent-runtime.deployment={prefix}"
                    },
                )
            )[0]
            await asyncio.to_thread(container.reload)
            assert container.attrs["Config"]["User"] == "10001:10001"
            assert container.attrs["HostConfig"]["NetworkMode"] == "bridge"
            assert container.attrs["HostConfig"]["Privileged"] is False
            assert container.attrs["HostConfig"]["ReadonlyRootfs"] is True
            assert all(
                "docker.sock" not in str(mount) for mount in container.attrs["Mounts"]
            )

            for _ in range(50):
                observation = await driver.status(handle, options=options)
                if observation.readiness is Readiness.CONFIRMED:
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("agent image did not become ready")

            image = await asyncio.to_thread(client.images.get, _IMAGE)
            image_config = image.attrs["Config"]
            assert image_config["User"] == "10001:10001"
            assert image_config["Entrypoint"] == ["/usr/local/bin/agent-runtime"]
            assert (
                image_config["Labels"]["io.universal-agent-runtime.qwen-code-image"]
                == _QWEN_IMAGE
            )
            assert not any(
                value.startswith(("QWEN_OLLAMA_", "OPENAI_API_KEY="))
                for value in image_config.get("Env", [])
            )
            qwen_version = await asyncio.to_thread(
                container.exec_run,
                ["qwen", "--version"],
                user="10001:10001",
                workdir="/workspace",
            )
            assert qwen_version.exit_code == 0
            assert "0.23.1" in _exec_output(qwen_version.output)
            no_ollama = await asyncio.to_thread(
                container.exec_run,
                [
                    "sh",
                    "-c",
                    "! command -v ollama >/dev/null 2>&1 && test ! -e /root/.ollama",
                ],
                user="10001:10001",
                workdir="/workspace",
            )
            assert no_ollama.exit_code == 0

            native_session = str(uuid4())
            codeword = f"EMBER_{uuid4().hex[:12].upper()}"
            first = await asyncio.to_thread(
                container.exec_run,
                [
                    "agent-runtime",
                    "turn",
                    "--session-id",
                    native_session,
                    "--prompt",
                    f"Remember the exact codeword {codeword}. Reply only ACK.",
                ],
                user="10001:10001",
                workdir="/workspace",
            )
            first_output = _exec_output(first.output)
            assert first.exit_code == 0, first_output
            assert '"type":"result"' in first_output

            stopped = await driver.stop(handle, options=options)
            assert stopped.execution is ExecutionState.INACTIVE
            restarted = await driver.start(handle, options=options)
            assert restarted.execution is ExecutionState.EXECUTING
            container_id = container.id
            assert container_id is not None
            container = await asyncio.to_thread(client.containers.get, container_id)
            second = await asyncio.to_thread(
                container.exec_run,
                [
                    "agent-runtime",
                    "turn",
                    "--resume",
                    native_session,
                    "--prompt",
                    "What exact codeword did I ask you to remember? Reply with only that codeword.",
                ],
                user="10001:10001",
                workdir="/workspace",
            )
            response = _exec_output(second.output)
            assert second.exit_code == 0, response
            assert codeword in response
            slot_check = await asyncio.to_thread(
                container.exec_run,
                [
                    "sh",
                    "-c",
                    "test -w /workspace && test -d /workspace/.agent/skills && test -d /workspace/.agent/tools",
                ],
                user="10001:10001",
                workdir="/workspace",
            )
            assert slot_check.exit_code == 0
        finally:
            if handle is not None:
                try:
                    await driver.stop(handle, options=options)
                except RuntimeFailure:
                    pass
                await driver.delete(handle, options=options)
                containers = await asyncio.to_thread(
                    client.containers.list,
                    all=True,
                    filters={
                        "label": f"io.universal-agent-runtime.deployment={prefix}"
                    },
                )
                volumes = await asyncio.to_thread(
                    client.volumes.list,
                    filters={
                        "label": f"io.universal-agent-runtime.deployment={prefix}"
                    },
                )
                assert not containers
                assert not volumes
            await driver.close()
            client.close()

    asyncio.run(scenario())
