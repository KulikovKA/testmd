"""In-memory reference double, not a selectable production runtime driver."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from tests.runtime_support.harness import Pause, Phase
from universal_agent_runtime.application.ports.agent_runtime import AgentRuntime
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeErrorCode as Code,
)
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeFailure,
)
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeOperation as Op,
)
from universal_agent_runtime.application.ports.runtime_values import (
    CreateRuntimeRequest,
    DeleteResult,
    OperationOptions,
    Readiness,
    ResourceLimits,
    RuntimeHandle,
    RuntimeObservation,
)
from universal_agent_runtime.application.ports.runtime_values import (
    ExecutionState as State,
)
from universal_agent_runtime.domain.identifiers import AgentId, WorkspaceId


@dataclass
class _Record:
    request: CreateRuntimeRequest
    handle: RuntimeHandle
    execution: State = State.INACTIVE
    readiness: Readiness = Readiness.UNCONFIRMED
    complete: bool = False
    deleted: bool = False
    runtime_exists: bool = True
    workspace_exists: bool = True
    workspace_data: str = ""

    def observation(self) -> RuntimeObservation:
        return RuntimeObservation(
            self.handle, self.request.workspace_id, self.execution, self.readiness
        )


class FakeRuntime:
    """Deterministic lifecycle oracle with explicit per-instance state.

    A single lock suffices for this double. Real drivers may serialize per identity.
    IDs are deterministic only here; they are not security tokens or backend IDs.
    """

    def __init__(self) -> None:
        self._records: dict[AgentId, _Record] = {}
        self._references: dict[UUID, _Record] = {}
        self._workspaces: dict[WorkspaceId, AgentId] = {}
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._faults: dict[tuple[Op, Phase], Code | Pause] = {}
        self._cleanup_failure = False

    @property
    def runtime(self) -> AgentRuntime:
        return self

    @property
    def options(self) -> OperationOptions:
        return OperationOptions(1)

    @property
    def interruption_options(self) -> OperationOptions:
        return OperationOptions(0.05)

    @property
    def resources_per_runtime(self) -> int:
        return 2

    def request(self, suffix: str = "one") -> CreateRuntimeRequest:
        return CreateRuntimeRequest(
            AgentId(f"agent-{suffix}"),
            WorkspaceId(f"workspace-{suffix}"),
            "test-workload",
            ResourceLimits(1.0, 64 * 1024 * 1024),
        )

    def fail_next(self, operation: Op, phase: Phase, code: Code) -> None:
        self._faults[operation, phase] = code

    def pause_next(self, operation: Op, phase: Phase) -> Pause:
        pause = Pause(asyncio.Event(), asyncio.Event())
        self._faults[operation, phase] = pause
        return pause

    def fail_cleanup_once(self) -> None:
        self._cleanup_failure = True

    async def _checkpoint(self, operation: Op, phase: Phase, agent_id: AgentId) -> None:
        fault = self._faults.pop((operation, phase), None)
        if isinstance(fault, Pause):
            fault.entered.set()
            await fault.release.wait()
        elif fault is not None:
            record = self._records.get(agent_id)
            raise RuntimeFailure(
                operation, agent_id, fault, record.handle if record else None
            )

    @asynccontextmanager
    async def _operation(
        self, operation: Op, agent_id: AgentId, options: OperationOptions
    ) -> AsyncIterator[None]:
        try:
            async with asyncio.timeout(options.timeout_seconds):
                async with self._lock:
                    await self._checkpoint(operation, Phase.BEFORE, agent_id)
                    yield
        except TimeoutError:
            record = self._records.get(agent_id)
            raise RuntimeFailure(
                operation, agent_id, Code.TIMEOUT, record.handle if record else None
            ) from None

    def _lookup(
        self, handle: RuntimeHandle, operation: Op, *, allow_absent: bool = False
    ) -> _Record | None:
        record = self._references.get(handle.reference)
        if record is not None and record.handle.agent_id != handle.agent_id:
            raise RuntimeFailure(operation, handle.agent_id, Code.NOT_FOUND)
        if record is None or record.deleted:
            if allow_absent:
                return None
            raise RuntimeFailure(operation, handle.agent_id, Code.NOT_FOUND)
        return record

    def _require(self, handle: RuntimeHandle, operation: Op) -> _Record:
        record = self._lookup(handle, operation)
        assert record is not None
        return record

    def _cleanup(self, record: _Record) -> bool:
        record.runtime_exists = False
        record.readiness = Readiness.UNCONFIRMED
        if self._cleanup_failure:
            self._cleanup_failure = False
            record.execution = State.FAULTED
            record.complete = False
            return False
        record.workspace_exists = False
        record.workspace_data = ""
        self._workspaces.pop(record.request.workspace_id, None)
        return True

    async def create(
        self, request: CreateRuntimeRequest, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.CREATE, request.agent_id, options):
            existing = self._records.get(request.agent_id)
            if existing is not None:
                if existing.deleted or existing.request != request:
                    raise RuntimeFailure(
                        Op.CREATE, request.agent_id, Code.CONFLICT, existing.handle
                    )
                if not existing.complete:
                    raise RuntimeFailure(
                        Op.CREATE,
                        request.agent_id,
                        Code.CLEANUP_FAILED,
                        existing.handle,
                    )
                return existing.observation()
            if request.workspace_id in self._workspaces:
                raise RuntimeFailure(Op.CREATE, request.agent_id, Code.CONFLICT)
            if request.workload != "test-workload":
                raise RuntimeFailure(
                    Op.CREATE, request.agent_id, Code.CONFIGURATION_REJECTED
                )
            self._sequence += 1
            record = _Record(
                request, RuntimeHandle(request.agent_id, UUID(int=self._sequence))
            )
            # Record ownership before the first cancellable provisioning checkpoint.
            self._records[request.agent_id] = record
            self._references[record.handle.reference] = record
            self._workspaces[request.workspace_id] = request.agent_id
            try:
                await self._checkpoint(Op.CREATE, Phase.ALLOCATED, request.agent_id)
                record.complete = True
                await self._checkpoint(Op.CREATE, Phase.COMMITTED, request.agent_id)
            except (RuntimeFailure, asyncio.CancelledError) as failure:
                if not record.complete:
                    cleaned = self._cleanup(record)
                    if cleaned:
                        del self._records[request.agent_id]
                        del self._references[record.handle.reference]
                    elif not isinstance(failure, asyncio.CancelledError):
                        raise RuntimeFailure(
                            Op.CREATE,
                            request.agent_id,
                            Code.CLEANUP_FAILED,
                            record.handle,
                        ) from None
                raise
            return record.observation()

    async def start(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.START, handle.agent_id, options):
            record = self._require(handle, Op.START)
            if not record.complete or record.execution not in {
                State.INACTIVE,
                State.EXECUTING,
            }:
                raise RuntimeFailure(
                    Op.START, handle.agent_id, Code.INVALID_STATE, handle
                )
            record.execution = State.EXECUTING
            await self._checkpoint(Op.START, Phase.COMMITTED, handle.agent_id)
            return record.observation()

    async def status(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.STATUS, handle.agent_id, options):
            return self._require(handle, Op.STATUS).observation()

    async def stop(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> RuntimeObservation:
        async with self._operation(Op.STOP, handle.agent_id, options):
            record = self._require(handle, Op.STOP)
            if not record.complete:
                raise RuntimeFailure(
                    Op.STOP, handle.agent_id, Code.INVALID_STATE, handle
                )
            record.execution = State.INACTIVE
            record.readiness = Readiness.UNCONFIRMED
            await self._checkpoint(Op.STOP, Phase.COMMITTED, handle.agent_id)
            return record.observation()

    async def delete(
        self, handle: RuntimeHandle, *, options: OperationOptions
    ) -> DeleteResult:
        async with self._operation(Op.DELETE, handle.agent_id, options):
            record = self._lookup(handle, Op.DELETE, allow_absent=True)
            if record is not None:
                if record.execution not in {State.INACTIVE, State.FAULTED}:
                    raise RuntimeFailure(
                        Op.DELETE, handle.agent_id, Code.INVALID_STATE, handle
                    )
                if not self._cleanup(record):
                    raise RuntimeFailure(
                        Op.DELETE, handle.agent_id, Code.CLEANUP_FAILED, handle
                    )
                record.deleted = True
                await self._checkpoint(Op.DELETE, Phase.COMMITTED, handle.agent_id)
            return DeleteResult(handle)

    async def confirm_ready(self, handle: RuntimeHandle) -> None:
        record = self._require(handle, Op.STATUS)
        if record.execution is not State.EXECUTING:
            raise ValueError("test readiness requires execution")
        record.readiness = Readiness.CONFIRMED

    async def force_state(self, handle: RuntimeHandle, state: State) -> None:
        record = self._require(handle, Op.STATUS)
        record.execution = state
        record.readiness = Readiness.UNCONFIRMED

    async def write_workspace(self, handle: RuntimeHandle, value: str) -> None:
        self._require(handle, Op.STATUS).workspace_data = value

    async def read_workspace(self, handle: RuntimeHandle) -> str:
        return self._require(handle, Op.STATUS).workspace_data

    async def resource_count(self) -> int:
        return sum(
            int(record.runtime_exists) + int(record.workspace_exists)
            for record in self._records.values()
        )

    async def close(self) -> None:
        self._records.clear()
        self._references.clear()
        self._workspaces.clear()
        self._faults.clear()
