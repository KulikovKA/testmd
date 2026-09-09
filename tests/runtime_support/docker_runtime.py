"""Real Docker harness for the reusable AgentRuntime conformance suite."""

import asyncio
import io
import tarfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import docker
from docker.errors import DockerException

from tests.runtime_support.harness import Pause, Phase
from universal_agent_runtime.adapters.docker_runtime import (
    DockerRuntime,
    DockerWorkload,
)
from universal_agent_runtime.application.ports.agent_runtime import AgentRuntime
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeErrorCode as Code,
)
from universal_agent_runtime.application.ports.runtime_errors import RuntimeFailure
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeOperation as Op,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    ExecutionState,
    OperationOptions,
    Readiness,
    ResourceLimits,
    RuntimeHandle,
    RuntimeObservation,
)
from universal_agent_runtime.domain.identifiers import AgentId, WorkspaceId

_IMAGE = "uar-task004-test:local"


def docker_available() -> bool:
    try:
        client = docker.from_env()
        client.ping()
        client.close()
    except DockerException:
        return False
    return True


def build_test_image() -> None:
    client = docker.from_env()
    try:
        root = Path(__file__).parents[1] / "docker_assets"
        client.images.build(path=str(root), tag=_IMAGE, rm=True, pull=False)
    finally:
        client.close()


class _InstrumentedDockerRuntime(DockerRuntime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.faults: dict[tuple[Op, Phase], Code | Pause] = {}
        self.cleanup_failure = False
        self.forced_states: dict[RuntimeHandle, ExecutionState] = {}

    async def _checkpoint(
        self, operation: Op, phase: object, agent_id: AgentId
    ) -> None:
        normalized = Phase(str(phase))
        fault = self.faults.pop((operation, normalized), None)
        if isinstance(fault, Pause):
            fault.entered.set()
            await fault.release.wait()
        elif fault is not None:
            record = self._records.get(agent_id)
            raise RuntimeFailure(
                operation, agent_id, fault, record.handle if record else None
            )
        if operation is Op.STOP and normalized is Phase.COMMITTED:
            record = self._records.get(agent_id)
            if record is not None:
                self.forced_states.pop(record.handle, None)

    async def _remove_volume(self, record: Any, operation: Op) -> None:
        if self.cleanup_failure:
            self.cleanup_failure = False
            raise RuntimeFailure(
                operation, record.request.agent_id, Code.OPERATION_FAILED
            )
        await super()._remove_volume(record, operation)

    async def _observation(self, record: Any, operation: Op) -> RuntimeObservation:
        forced = self.forced_states.get(record.handle)
        if forced is not None:
            return RuntimeObservation(
                record.handle,
                record.request.workspace_id,
                forced,
                Readiness.UNCONFIRMED,
            )
        return await super()._observation(record, operation)


class DockerRuntimeHarness:
    def __init__(self) -> None:
        self.prefix = f"uar-t004-{uuid4().hex[:12]}"
        healthcheck = {
            "test": ["CMD-SHELL", "test -f /tmp/ready"],
            "interval": 100_000_000,
            "timeout": 100_000_000,
            "retries": 3,
            "start_period": 100_000_000,
        }
        self._runtime = _InstrumentedDockerRuntime(
            {
                "test-workload": DockerWorkload(
                    _IMAGE,
                    (
                        "sh",
                        "-c",
                        "rm -f /tmp/ready; trap 'rm -f /tmp/ready; exit 0' TERM INT; while :; do sleep 1; done",
                    ),
                    "65534:65534",
                    healthcheck=healthcheck,
                )
            },
            resource_prefix=self.prefix,
        )

    @property
    def runtime(self) -> AgentRuntime:
        return self._runtime

    @property
    def options(self) -> OperationOptions:
        return OperationOptions(10)

    @property
    def interruption_options(self) -> OperationOptions:
        # Leave enough time for the real daemon effect to reach the injected
        # checkpoint before the deliberately lost response is timed out.
        return OperationOptions(2)

    @property
    def resources_per_runtime(self) -> int:
        return 2

    def request(self, suffix: str = "one") -> CreateRuntimeRequest:
        return CreateRuntimeRequest(
            AgentId(f"agent-{suffix}"),
            WorkspaceId(f"workspace-{suffix}"),
            "test-workload",
            ResourceLimits(0.25, 32 * 1024 * 1024),
        )

    def fail_next(self, operation: Op, phase: Phase, code: Code) -> None:
        self._runtime.faults[operation, phase] = code

    def pause_next(self, operation: Op, phase: Phase) -> Pause:
        pause = Pause(asyncio.Event(), asyncio.Event())
        self._runtime.faults[operation, phase] = pause
        return pause

    def fail_cleanup_once(self) -> None:
        self._runtime.cleanup_failure = True

    async def confirm_ready(self, handle: RuntimeHandle) -> None:
        record = self._runtime._require(handle, Op.STATUS)
        container = await self._runtime._container(record, Op.STATUS)
        result = await asyncio.to_thread(container.exec_run, ["touch", "/tmp/ready"])
        if result.exit_code != 0:
            raise AssertionError("test readiness signal failed")
        for _ in range(50):
            observation = await self._runtime.status(handle, options=self.options)
            if observation.readiness is Readiness.CONFIRMED:
                return
            await asyncio.sleep(0.1)
        raise AssertionError("test container did not become healthy")

    async def force_state(self, handle: RuntimeHandle, state: ExecutionState) -> None:
        self._runtime._require(handle, Op.STATUS)
        self._runtime.forced_states[handle] = state

    async def write_workspace(self, handle: RuntimeHandle, value: str) -> None:
        record = self._runtime._require(handle, Op.STATUS)
        container = await self._runtime._container(record, Op.STATUS)
        payload = value.encode("utf-8")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo("contract-value.txt")
            info.size = len(payload)
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(payload))
        await asyncio.to_thread(container.put_archive, "/workspace", archive.getvalue())

    async def read_workspace(self, handle: RuntimeHandle) -> str:
        record = self._runtime._require(handle, Op.STATUS)
        container = await self._runtime._container(record, Op.STATUS)
        chunks, _ = await asyncio.to_thread(
            container.get_archive, "/workspace/contract-value.txt"
        )
        archive = io.BytesIO(b"".join(chunks))
        with tarfile.open(fileobj=archive, mode="r") as tar:
            member = tar.getmember("contract-value.txt")
            extracted = tar.extractfile(member)
            assert extracted is not None
            return extracted.read().decode("utf-8")

    async def resource_count(self) -> int:
        client = self._runtime._client
        label = f"io.universal-agent-runtime.deployment={self.prefix}"
        containers = await asyncio.to_thread(
            lambda: client.containers.list(all=True, filters={"label": label})
        )
        volumes = await asyncio.to_thread(
            lambda: client.volumes.list(filters={"label": label})
        )
        return len(containers) + len(volumes)

    async def close(self) -> None:
        for record in list(self._runtime._records.values()):
            try:
                await self._runtime._remove_container(record, Op.DELETE)
            except RuntimeFailure:
                pass
            try:
                await self._runtime._remove_volume(record, Op.DELETE)
            except RuntimeFailure:
                pass
        await self._runtime.close()
