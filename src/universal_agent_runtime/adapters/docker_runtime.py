"""Docker implementation of the runtime-neutral AgentRuntime lifecycle port."""

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeErrorCode as Code,
)
from universal_agent_runtime.application.ports.runtime_errors import RuntimeFailure
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeOperation as Op,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    DeleteResult,
    ExecutionState,
    OperationOptions,
    Readiness,
    RuntimeHandle,
    RuntimeObservation,
)
from universal_agent_runtime.domain.identifiers import AgentId, WorkspaceId


@dataclass(frozen=True)
class DockerWorkload:
    """Deployment-owned Docker settings selected by a logical workload key."""

    image: str
    command: tuple[str, ...]
    user: str
    workspace_target: str = "/workspace"
    healthcheck: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.image or not self.command or not self.user:
            raise ValueError("Docker workload image, command and user are required")
        if not self.workspace_target.startswith("/"):
            raise ValueError("workspace_target must be an absolute container path")


@dataclass
class _Record:
    request: CreateRuntimeRequest
    handle: RuntimeHandle
    volume_name: str
    container_name: str
    complete: bool = False
    deleted: bool = False
    container_exists: bool = False
    volume_exists: bool = False
    cleanup_required: bool = False


class DockerRuntime:
    """Local Docker driver; Docker objects and identifiers stay inside this adapter."""

    _LABEL_PREFIX = "io.universal-agent-runtime"

    def __init__(
        self,
        workloads: Mapping[str, DockerWorkload],
        *,
        secret_resolver: Callable[[str], str] | None = None,
        client: Any | None = None,
        resource_prefix: str = "uar",
    ) -> None:
        if not workloads:
            raise ValueError("at least one Docker workload is required")
        if not resource_prefix or not resource_prefix.replace("-", "").isalnum():
            raise ValueError("invalid Docker resource prefix")
        self._workloads = dict(workloads)
        self._secret_resolver = secret_resolver
        self._client = client if client is not None else docker.from_env()
        self._prefix = resource_prefix
        self._records: dict[AgentId, _Record] = {}
        self._references: dict[UUID, _Record] = {}
        self._workspaces: dict[WorkspaceId, AgentId] = {}
        self._retired: set[UUID] = set()
        self._lock = asyncio.Lock()

    async def _checkpoint(
        self, operation: Op, phase: object, agent_id: AgentId
    ) -> None:
        """No-op extension point used only by the Docker integration harness."""

    def _failure(
        self,
        operation: Op,
        agent_id: AgentId,
        code: Code,
        handle: RuntimeHandle | None = None,
    ) -> RuntimeFailure:
        return RuntimeFailure(operation, agent_id, code, handle)

    async def _docker_call(
        self,
        operation: Op,
        agent_id: AgentId,
        function: Callable[..., Any],
        *args: Any,
        handle: RuntimeHandle | None = None,
        **kwargs: Any,
    ) -> Any:
        backend_task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            # A cancelled asyncio caller cannot stop an SDK call already running in
            # a worker thread. Wait for that effect so create can reconcile ownership.
            return await asyncio.shield(backend_task)
        except asyncio.CancelledError:
            try:
                await backend_task
            except DockerException:
                pass
            raise
        except ImageNotFound:
            raise self._failure(
                operation, agent_id, Code.CONFIGURATION_REJECTED, handle
            ) from None
        except NotFound:
            raise self._failure(operation, agent_id, Code.NOT_FOUND, handle) from None
        except APIError as error:
            status = getattr(error, "status_code", None)
            code = Code.CONFLICT if status == 409 else Code.OPERATION_FAILED
            raise self._failure(operation, agent_id, code, handle) from None
        except DockerException:
            raise self._failure(operation, agent_id, Code.UNAVAILABLE, handle) from None

    @asynccontextmanager
    async def _operation(
        self, operation: Op, agent_id: AgentId, options: OperationOptions
    ) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout(options.timeout_seconds):
                async with self._lock:
                    await self._checkpoint(operation, "before", agent_id)
                    yield
        except TimeoutError:
            record = self._records.get(agent_id)
            raise self._failure(
                operation,
                agent_id,
                Code.TIMEOUT,
                record.handle if record is not None else None,
            ) from None

    def _labels(self, record: _Record, kind: str) -> dict[str, str]:
        return {
            f"{self._LABEL_PREFIX}.managed": "true",
            f"{self._LABEL_PREFIX}.deployment": self._prefix,
            f"{self._LABEL_PREFIX}.kind": kind,
            f"{self._LABEL_PREFIX}.agent": record.request.agent_id.value,
            f"{self._LABEL_PREFIX}.workspace": record.request.workspace_id.value,
            f"{self._LABEL_PREFIX}.reference": str(record.handle.reference),
        }

    def _lookup(
        self, handle: RuntimeHandle, operation: Op, *, allow_absent: bool = False
    ) -> _Record | None:
        record = self._references.get(handle.reference)
        if record is not None and record.handle.agent_id != handle.agent_id:
            raise self._failure(operation, handle.agent_id, Code.NOT_FOUND)
        if record is None or record.deleted:
            if allow_absent or handle.reference in self._retired:
                return None
            raise self._failure(operation, handle.agent_id, Code.NOT_FOUND)
        return record

    def _require(self, handle: RuntimeHandle, operation: Op) -> _Record:
        record = self._lookup(handle, operation)
        if record is None:
            raise self._failure(operation, handle.agent_id, Code.NOT_FOUND)
        return record

    def _validate_request(self, request: CreateRuntimeRequest) -> DockerWorkload:
        workload = self._workloads.get(request.workload)
        if workload is None or request.network:
            raise self._failure(
                Op.CREATE, request.agent_id, Code.CONFIGURATION_REJECTED
            )
        if request.secrets and self._secret_resolver is None:
            raise self._failure(
                Op.CREATE, request.agent_id, Code.CONFIGURATION_REJECTED
            )
        return workload

    def _environment(self, request: CreateRuntimeRequest) -> dict[str, str]:
        values = {entry.name: entry.value for entry in request.environment}
        if self._secret_resolver is not None:
            for binding in request.secrets:
                try:
                    value = self._secret_resolver(binding.secret_id)
                # A deployment resolver is an injected trust boundary and may use
                # arbitrary providers; none of their exceptions may cross the port.
                except Exception:  # noqa: BLE001
                    raise self._failure(
                        Op.CREATE, request.agent_id, Code.CONFIGURATION_REJECTED
                    ) from None
                if not isinstance(value, str) or "\x00" in value:
                    raise self._failure(
                        Op.CREATE, request.agent_id, Code.CONFIGURATION_REJECTED
                    )
                values[binding.name] = value
        return values

    async def _container(self, record: _Record, operation: Op) -> Any:
        return await self._docker_call(
            operation,
            record.request.agent_id,
            self._client.containers.get,
            record.container_name,
            handle=record.handle,
        )

    async def _observation(self, record: _Record, operation: Op) -> RuntimeObservation:
        if record.cleanup_required or not record.complete:
            state = ExecutionState.FAULTED
            readiness = Readiness.UNCONFIRMED
        else:
            container = await self._container(record, operation)
            await self._docker_call(
                operation,
                record.request.agent_id,
                container.reload,
                handle=record.handle,
            )
            raw_state = container.attrs.get("State", {})
            raw_status = raw_state.get("Status")
            if raw_status == "running":
                state = ExecutionState.EXECUTING
            elif raw_status in {"created", "exited", "dead"}:
                exit_code = raw_state.get("ExitCode", 0)
                state = (
                    ExecutionState.FAULTED
                    if raw_status in {"dead"}
                    or (raw_status == "exited" and exit_code not in {0, 137, 143})
                    else ExecutionState.INACTIVE
                )
            else:
                state = ExecutionState.UNKNOWN
            health = raw_state.get("Health", {}).get("Status")
            readiness = (
                Readiness.CONFIRMED
                if state is ExecutionState.EXECUTING and health == "healthy"
                else Readiness.UNCONFIRMED
            )
        return RuntimeObservation(
            record.handle, record.request.workspace_id, state, readiness
        )

    async def _remove_container(self, record: _Record, operation: Op) -> None:
        if not record.container_exists:
            return
        try:
            container = await self._container(record, operation)
            await self._docker_call(
                operation,
                record.request.agent_id,
                container.remove,
                force=True,
                v=False,
                handle=record.handle,
            )
        except RuntimeFailure as error:
            if error.code is not Code.NOT_FOUND:
                raise
        record.container_exists = False

    async def _remove_volume(self, record: _Record, operation: Op) -> None:
        if not record.volume_exists:
            return
        try:
            volume = await self._docker_call(
                operation,
                record.request.agent_id,
                self._client.volumes.get,
                record.volume_name,
                handle=record.handle,
            )
            await self._docker_call(
                operation,
                record.request.agent_id,
                volume.remove,
                force=True,
                handle=record.handle,
            )
        except RuntimeFailure as error:
            if error.code is not Code.NOT_FOUND:
                raise
        record.volume_exists = False

    async def _cleanup(self, record: _Record, operation: Op) -> bool:
        try:
            await self._remove_container(record, operation)
            await self._remove_volume(record, operation)
        except RuntimeFailure:
            record.cleanup_required = True
            record.complete = False
            return False
        record.cleanup_required = False
        return True

    async def _reconcile_allocations(self, record: _Record) -> None:
        async def exists(collection: Any, name: str, current: bool) -> bool:
            try:
                await asyncio.to_thread(collection.get, name)
                return True
            except NotFound:
                return False
            except DockerException:
                # Preserve conservative ownership when the control path is uncertain.
                return current

        record.container_exists = await exists(
            self._client.containers, record.container_name, record.container_exists
        )
        record.volume_exists = await exists(
            self._client.volumes, record.volume_name, record.volume_exists
        )

    async def create(
        self, request: CreateRuntimeRequest, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.CREATE, request.agent_id, options):
            existing = self._records.get(request.agent_id)
            if existing is not None:
                if existing.deleted or existing.request != request:
                    raise self._failure(
                        Op.CREATE, request.agent_id, Code.CONFLICT, existing.handle
                    )
                if existing.cleanup_required or not existing.complete:
                    raise self._failure(
                        Op.CREATE,
                        request.agent_id,
                        Code.CLEANUP_FAILED,
                        existing.handle,
                    )
                return await self._observation(existing, Op.CREATE)
            if request.workspace_id in self._workspaces:
                raise self._failure(Op.CREATE, request.agent_id, Code.CONFLICT)
            workload = self._validate_request(request)
            handle = RuntimeHandle(request.agent_id, uuid4())
            token = handle.reference.hex
            record = _Record(
                request,
                handle,
                f"{self._prefix}-workspace-{token}",
                f"{self._prefix}-runtime-{token}",
            )
            self._records[request.agent_id] = record
            self._references[handle.reference] = record
            self._workspaces[request.workspace_id] = request.agent_id
            try:
                await self._docker_call(
                    Op.CREATE,
                    request.agent_id,
                    self._client.volumes.create,
                    name=record.volume_name,
                    labels=self._labels(record, "workspace"),
                    handle=handle,
                )
                record.volume_exists = True
                await self._checkpoint(Op.CREATE, "allocated", request.agent_id)
                await self._docker_call(
                    Op.CREATE,
                    request.agent_id,
                    self._client.containers.create,
                    workload.image,
                    list(workload.command),
                    name=record.container_name,
                    user=workload.user,
                    environment=self._environment(request),
                    labels=self._labels(record, "runtime"),
                    network_disabled=True,
                    network_mode="none",
                    nano_cpus=int(request.resources.cpu_cores * 1_000_000_000),
                    mem_limit=request.resources.memory_bytes,
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    pids_limit=128,
                    tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                    volumes={
                        record.volume_name: {
                            "bind": workload.workspace_target,
                            "mode": "rw",
                        }
                    },
                    healthcheck=dict(workload.healthcheck)
                    if workload.healthcheck
                    else None,
                    detach=True,
                    init=True,
                    stdin_open=False,
                    tty=False,
                    privileged=False,
                    auto_remove=False,
                    handle=handle,
                )
                record.container_exists = True
                record.complete = True
                await self._checkpoint(Op.CREATE, "committed", request.agent_id)
            except (RuntimeFailure, asyncio.CancelledError) as failure:
                if not record.complete:
                    await self._reconcile_allocations(record)
                    cleaned = await self._cleanup(record, Op.CREATE)
                    if cleaned:
                        self._records.pop(request.agent_id, None)
                        self._references.pop(handle.reference, None)
                        self._workspaces.pop(request.workspace_id, None)
                    elif not isinstance(failure, asyncio.CancelledError):
                        raise self._failure(
                            Op.CREATE, request.agent_id, Code.CLEANUP_FAILED, handle
                        ) from None
                raise
            return await self._observation(record, Op.CREATE)

    async def start(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.START, handle.agent_id, options):
            record = self._require(handle, Op.START)
            observation = await self._observation(record, Op.START)
            if observation.execution not in {
                ExecutionState.INACTIVE,
                ExecutionState.EXECUTING,
            }:
                raise self._failure(
                    Op.START, handle.agent_id, Code.INVALID_STATE, handle
                )
            if observation.execution is ExecutionState.INACTIVE:
                container = await self._container(record, Op.START)
                await self._docker_call(
                    Op.START, handle.agent_id, container.start, handle=handle
                )
            await self._checkpoint(Op.START, "committed", handle.agent_id)
            return await self._observation(record, Op.START)

    async def status(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.STATUS, handle.agent_id, options):
            return await self._observation(self._require(handle, Op.STATUS), Op.STATUS)

    async def stop(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.STOP, handle.agent_id, options):
            record = self._require(handle, Op.STOP)
            if not record.complete or record.cleanup_required:
                raise self._failure(
                    Op.STOP, handle.agent_id, Code.INVALID_STATE, handle
                )
            observation = await self._observation(record, Op.STOP)
            if observation.execution is not ExecutionState.INACTIVE:
                container = await self._container(record, Op.STOP)
                await self._docker_call(
                    Op.STOP, handle.agent_id, container.stop, timeout=1, handle=handle
                )
            await self._checkpoint(Op.STOP, "committed", handle.agent_id)
            return await self._observation(record, Op.STOP)

    async def delete(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> DeleteResult:
        async with self._operation(Op.DELETE, handle.agent_id, options):
            record = self._lookup(handle, Op.DELETE, allow_absent=True)
            if record is not None:
                observation = await self._observation(record, Op.DELETE)
                if observation.execution not in {
                    ExecutionState.INACTIVE,
                    ExecutionState.FAULTED,
                }:
                    raise self._failure(
                        Op.DELETE, handle.agent_id, Code.INVALID_STATE, handle
                    )
                if not await self._cleanup(record, Op.DELETE):
                    raise self._failure(
                        Op.DELETE, handle.agent_id, Code.CLEANUP_FAILED, handle
                    )
                record.deleted = True
                self._retired.add(handle.reference)
                await self._checkpoint(Op.DELETE, "committed", handle.agent_id)
            return DeleteResult(handle)

    async def close(self) -> None:
        """Close the Docker SDK client without deleting managed runtimes."""
        await asyncio.to_thread(self._client.close)
