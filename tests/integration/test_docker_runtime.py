import asyncio
from uuid import uuid4

import docker
import pytest

from tests.runtime_support.docker_runtime import (
    DockerRuntimeHarness,
    build_test_image,
    docker_available,
)
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeErrorCode,
    RuntimeFailure,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    EnvironmentVariable,
    ExecutionState,
    NetworkDestination,
    OperationOptions,
    ResourceLimits,
    SecretBinding,
)
from universal_agent_runtime.domain.identifiers import AgentId, WorkspaceId

pytestmark = pytest.mark.skipif(
    not docker_available(), reason="local Docker daemon is unavailable"
)


def test_real_docker_lifecycle_cleanup_and_security_mapping() -> None:
    build_test_image()
    prefix = f"uar-security-{uuid4().hex[:12]}"
    driver = DockerRuntime(
        {
            "safe-fixture": DockerWorkload(
                "uar-task004-test:local",
                ("sh", "-c", "trap 'exit 0' TERM INT; while :; do sleep 1; done"),
                "65534:65534",
            )
        },
        secret_resolver=lambda secret_id: f"resolved-{secret_id}",
        resource_prefix=prefix,
    )
    request = CreateRuntimeRequest(
        AgentId("integration-agent"),
        WorkspaceId("integration-workspace"),
        "safe-fixture",
        ResourceLimits(0.25, 32 * 1024 * 1024),
        environment=(EnvironmentVariable("PLAIN_VALUE", "plain"),),
        secrets=(SecretBinding("SECRET_VALUE", "fixture-secret"),),
    )
    options = OperationOptions(10)

    async def scenario() -> None:
        client = docker.from_env()
        handle = None
        try:
            created = await driver.create(request, options=options)
            handle = created.handle
            assert created.execution is ExecutionState.INACTIVE
            containers = await asyncio.to_thread(
                client.containers.list,
                all=True,
                filters={"label": f"io.universal-agent-runtime.deployment={prefix}"},
            )
            assert len(containers) == 1
            container = containers[0]
            await asyncio.to_thread(container.reload)
            host = container.attrs["HostConfig"]
            config = container.attrs["Config"]
            mounts = container.attrs["Mounts"]
            assert host["Privileged"] is False
            assert host["ReadonlyRootfs"] is True
            assert host["CapDrop"] == ["ALL"]
            assert host["NetworkMode"] == "none"
            assert host["Memory"] == request.resources.memory_bytes
            assert host["NanoCpus"] == 250_000_000
            assert host["PidsLimit"] == 128
            assert any("no-new-privileges" in item for item in host["SecurityOpt"])
            assert len(mounts) == 1
            assert mounts[0]["Type"] == "volume"
            assert mounts[0]["Destination"] == "/workspace"
            assert all("docker.sock" not in str(mount) for mount in mounts)
            assert "PLAIN_VALUE=plain" in config["Env"]
            assert "SECRET_VALUE=resolved-fixture-secret" in config["Env"]

            started = await driver.start(handle, options=options)
            assert started.execution is ExecutionState.EXECUTING
            stopped = await driver.stop(handle, options=options)
            assert stopped.execution is ExecutionState.INACTIVE
            await driver.delete(handle, options=options)
            assert not await asyncio.to_thread(
                client.containers.list,
                all=True,
                filters={"label": f"io.universal-agent-runtime.deployment={prefix}"},
            )
            assert not await asyncio.to_thread(
                client.volumes.list,
                filters={"label": f"io.universal-agent-runtime.deployment={prefix}"},
            )
        finally:
            if handle is not None:
                try:
                    await driver.stop(handle, options=options)
                except RuntimeFailure:
                    pass
                await driver.delete(handle, options=options)
            await driver.close()
            client.close()

    asyncio.run(scenario())


def test_unenforceable_destination_is_rejected_without_resources() -> None:
    build_test_image()
    harness = DockerRuntimeHarness()

    async def scenario() -> None:
        request = harness.request()
        request = CreateRuntimeRequest(
            request.agent_id,
            request.workspace_id,
            request.workload,
            request.resources,
            network=(NetworkDestination("example.invalid", 443),),
        )
        try:
            with pytest.raises(RuntimeFailure) as error:
                await harness.runtime.create(request, options=harness.options)
            assert error.value.code is RuntimeErrorCode.CONFIGURATION_REJECTED
            assert await harness.resource_count() == 0
        finally:
            await harness.close()

    asyncio.run(scenario())
