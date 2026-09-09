"""Runtime-neutral Agent lifecycle use cases for TASK-009."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from uuid import uuid4

from universal_agent_runtime.application.ports.agent_interaction import AgentInteraction
from universal_agent_runtime.application.ports.agent_repository import AgentRepository
from universal_agent_runtime.application.ports.agent_runtime import AgentRuntime
from universal_agent_runtime.application.ports.interaction_errors import (
    InteractionErrorCode,
    InteractionFailure,
)
from universal_agent_runtime.application.ports.interaction_values import (
    SessionReference,
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
    Readiness,
    ResourceLimits,
    RuntimeHandle,
    RuntimeObservation,
    SecretBinding,
)
from universal_agent_runtime.domain.agent import AgentLifecycleState
from universal_agent_runtime.domain.identifiers import (
    AgentId,
    SessionId,
    WorkspaceId,
    validate_identifier,
)
from universal_agent_runtime.domain.message import Message


class AgentLifecycleOperation(str, Enum):
    CREATE = "create"
    INSPECT = "inspect"
    START = "start"
    STOP = "stop"
    DELETE = "delete"
    MESSAGE = "message"
    HISTORY = "history"


class AgentLifecycleErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    INVALID_STATE = "invalid_state"
    CONFLICT = "conflict"
    CONFIGURATION_REJECTED = "configuration_rejected"
    RUNTIME_NOT_FOUND = "runtime_not_found"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    TIMEOUT = "timeout"
    READINESS_TIMEOUT = "readiness_timeout"
    CLEANUP_FAILED = "cleanup_failed"
    SESSION_FAILED = "session_failed"
    OPERATION_FAILED = "operation_failed"
    MESSAGE_INVALID = "message_invalid"
    HISTORY_LIMIT = "history_limit"
    INFERENCE_UNAVAILABLE = "inference_unavailable"
    TOOL_FAILED = "tool_failed"
    INTERACTION_FAILED = "interaction_failed"


@dataclass(frozen=True)
class AgentFailure:
    operation: AgentLifecycleOperation
    code: AgentLifecycleErrorCode
    retryable: bool
    interaction_code: InteractionErrorCode | None = None


@dataclass(frozen=True)
class AgentConfigurationSnapshot:
    """Safe per-Agent configuration/capability snapshot."""

    workload: str
    resources: ResourceLimits
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_identifier(self.workload)
        if not isinstance(self.resources, ResourceLimits):
            raise TypeError("invalid resource limits")
        for values in (self.skills, self.tools):
            if not isinstance(values, tuple) or len(values) > 32:
                raise ValueError(
                    "capabilities must be immutable tuples of at most 32 IDs"
                )
            for value in values:
                validate_identifier(value)
            if len(values) != len(set(values)):
                raise ValueError("capability IDs must not contain duplicates")


@dataclass(frozen=True)
class CreateAgentCommand:
    request_id: str
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_identifier(self.request_id)
        for values in (self.skills, self.tools):
            if not isinstance(values, tuple):
                raise TypeError("capability IDs must be immutable tuples")
            for value in values:
                validate_identifier(value)
            if len(values) > 32 or len(values) != len(set(values)):
                raise ValueError("capability IDs must be unique and limited to 32")


@dataclass(frozen=True)
class LifecycleConfiguration:
    workload: str
    resources: ResourceLimits
    environment: tuple[EnvironmentVariable, ...]
    secrets: tuple[SecretBinding, ...]
    network: tuple[NetworkDestination, ...]
    operation_options: OperationOptions
    readiness_timeout_seconds: float
    readiness_poll_interval_seconds: float

    def __post_init__(self) -> None:
        validate_identifier(self.workload)
        if not isinstance(self.resources, ResourceLimits) or not isinstance(
            self.operation_options, OperationOptions
        ):
            raise TypeError("invalid lifecycle configuration")
        for values, expected in (
            (self.environment, EnvironmentVariable),
            (self.secrets, SecretBinding),
            (self.network, NetworkDestination),
        ):
            if not isinstance(values, tuple) or not all(
                isinstance(value, expected) for value in values
            ):
                raise TypeError("invalid lifecycle configuration values")
        for value in (
            self.readiness_timeout_seconds,
            self.readiness_poll_interval_seconds,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError("readiness timings must be positive")


@dataclass(frozen=True)
class AgentRecord:
    agent_id: AgentId
    workspace_id: WorkspaceId
    session: SessionReference
    creation_request_id: str
    configuration: AgentConfigurationSnapshot
    runtime_request: CreateRuntimeRequest
    state: AgentLifecycleState
    runtime_handle: RuntimeHandle | None = None
    runtime_observation: RuntimeObservation | None = None
    failure: AgentFailure | None = None
    messages: tuple[Message, ...] = ()
    conversation_recovery_required: bool = False

    def __post_init__(self) -> None:
        if self.session.agent_id != self.agent_id:
            raise ValueError("Session must belong to the Agent")
        if self.runtime_request.agent_id != self.agent_id:
            raise ValueError("runtime request must belong to the Agent")
        if self.runtime_request.workspace_id != self.workspace_id:
            raise ValueError("runtime request must use the Agent workspace")
        if (
            self.runtime_handle is not None
            and self.runtime_handle.agent_id != self.agent_id
        ):
            raise ValueError("runtime handle must belong to the Agent")
        if self.runtime_observation is not None and (
            self.runtime_handle is None
            or self.runtime_observation.handle != self.runtime_handle
        ):
            raise ValueError("runtime observation must match the Agent handle")


@dataclass(frozen=True)
class CreateAgentResult:
    agent: AgentRecord
    created: bool


@dataclass(frozen=True)
class DeleteAgentResult:
    agent_id: AgentId
    already_deleted: bool


class AgentLifecycleFailure(Exception):
    """Safe application failure without backend or session implementation detail."""

    def __init__(
        self,
        operation: AgentLifecycleOperation,
        code: AgentLifecycleErrorCode,
        *,
        agent_id: AgentId | None = None,
        state: AgentLifecycleState | None = None,
        retryable: bool = False,
        interaction_code: InteractionErrorCode | None = None,
    ) -> None:
        self.operation = operation
        self.code = code
        self.agent_id = agent_id
        self.state = state
        self.retryable = retryable
        self.interaction_code = interaction_code
        super().__init__(f"{operation.value}: {code.value}")


def _identifier_token() -> str:
    return uuid4().hex


class AgentLifecycleService:
    """Coordinate lifecycle state through project-owned ports only."""

    def __init__(
        self,
        runtime: AgentRuntime,
        interaction: AgentInteraction,
        repository: AgentRepository,
        configuration: LifecycleConfiguration,
        *,
        identifier_factory: Callable[[], str] = _identifier_token,
    ) -> None:
        self._runtime = runtime
        self._interaction = interaction
        self._repository = repository
        self._configuration = configuration
        self._identifier_factory = identifier_factory
        self._creation_lock = asyncio.Lock()
        self._agent_locks: dict[AgentId, asyncio.Lock] = {}

    def _lock(self, agent_id: AgentId) -> asyncio.Lock:
        return self._agent_locks.setdefault(agent_id, asyncio.Lock())

    def _new_identities(self) -> tuple[AgentId, WorkspaceId, SessionReference]:
        for _ in range(100):
            token = self._identifier_factory()
            if not isinstance(token, str) or not token or len(token) > 48:
                raise ValueError("identifier factory returned an invalid token")
            validate_identifier(token)
            agent_id = AgentId(f"agent-{token}")
            if self._repository.get(
                agent_id
            ) is not None or self._repository.was_deleted(agent_id):
                continue
            workspace_id = WorkspaceId(f"workspace-{token}")
            session = SessionReference(agent_id, SessionId(f"session-{token}"))
            return agent_id, workspace_id, session
        raise RuntimeError("identifier factory could not allocate a unique Agent ID")

    def _require(
        self, agent_id: AgentId, operation: AgentLifecycleOperation
    ) -> AgentRecord:
        record = self._repository.get(agent_id)
        if record is None:
            raise AgentLifecycleFailure(
                operation, AgentLifecycleErrorCode.NOT_FOUND, agent_id=agent_id
            )
        return record

    def _runtime_failure(
        self, operation: AgentLifecycleOperation, failure: RuntimeFailure
    ) -> AgentLifecycleFailure:
        mapping = {
            RuntimeErrorCode.CONFIGURATION_REJECTED: AgentLifecycleErrorCode.CONFIGURATION_REJECTED,
            RuntimeErrorCode.NOT_FOUND: AgentLifecycleErrorCode.RUNTIME_NOT_FOUND,
            RuntimeErrorCode.INVALID_STATE: AgentLifecycleErrorCode.INVALID_STATE,
            RuntimeErrorCode.CONFLICT: AgentLifecycleErrorCode.CONFLICT,
            RuntimeErrorCode.UNAVAILABLE: AgentLifecycleErrorCode.RUNTIME_UNAVAILABLE,
            RuntimeErrorCode.TIMEOUT: AgentLifecycleErrorCode.TIMEOUT,
            RuntimeErrorCode.CLEANUP_FAILED: AgentLifecycleErrorCode.CLEANUP_FAILED,
            RuntimeErrorCode.OPERATION_FAILED: AgentLifecycleErrorCode.OPERATION_FAILED,
        }
        return AgentLifecycleFailure(
            operation,
            mapping[failure.code],
            agent_id=failure.agent_id,
            state=AgentLifecycleState.FAILED,
            retryable=failure.retryable,
        )

    def _save_failure(
        self,
        record: AgentRecord,
        failure: AgentLifecycleFailure,
        *,
        handle: RuntimeHandle | None = None,
        observation: RuntimeObservation | None = None,
    ) -> None:
        self._repository.save(
            replace(
                record,
                state=AgentLifecycleState.FAILED,
                runtime_handle=handle if handle is not None else record.runtime_handle,
                runtime_observation=(
                    observation
                    if observation is not None
                    else record.runtime_observation
                ),
                failure=AgentFailure(
                    failure.operation, failure.code, failure.retryable
                ),
            )
        )

    async def create(self, command: CreateAgentCommand) -> CreateAgentResult:
        async with self._creation_lock:
            owner = self._repository.owner_of_creation_request(command.request_id)
            if owner is not None:
                existing = self._repository.get(owner)
                if existing is None:
                    raise AgentLifecycleFailure(
                        AgentLifecycleOperation.CREATE,
                        AgentLifecycleErrorCode.CONFLICT,
                        agent_id=owner,
                    )
                if (
                    existing.configuration.skills != command.skills
                    or existing.configuration.tools != command.tools
                ):
                    raise AgentLifecycleFailure(
                        AgentLifecycleOperation.CREATE,
                        AgentLifecycleErrorCode.CONFLICT,
                        agent_id=owner,
                        state=existing.state,
                    )
                return CreateAgentResult(existing, False)

            agent_id, workspace_id, session = self._new_identities()
            snapshot = AgentConfigurationSnapshot(
                self._configuration.workload,
                self._configuration.resources,
                command.skills,
                command.tools,
            )
            runtime_request = CreateRuntimeRequest(
                agent_id,
                workspace_id,
                self._configuration.workload,
                self._configuration.resources,
                self._configuration.environment,
                self._configuration.secrets,
                self._configuration.network,
            )
            record = AgentRecord(
                agent_id,
                workspace_id,
                session,
                command.request_id,
                snapshot,
                runtime_request,
                AgentLifecycleState.CREATING,
            )
            self._repository.add(record)
            try:
                observation = await self._runtime.create(
                    runtime_request, options=self._configuration.operation_options
                )
            except RuntimeFailure as runtime_failure:
                failure = self._runtime_failure(
                    AgentLifecycleOperation.CREATE, runtime_failure
                )
                self._save_failure(record, failure, handle=runtime_failure.handle)
                raise failure from None

            record = replace(
                record,
                runtime_handle=observation.handle,
                runtime_observation=observation,
            )
            self._repository.save(record)
            if observation.execution is not ExecutionState.INACTIVE:
                failure = AgentLifecycleFailure(
                    AgentLifecycleOperation.CREATE,
                    AgentLifecycleErrorCode.OPERATION_FAILED,
                    agent_id=agent_id,
                    state=AgentLifecycleState.FAILED,
                )
                self._save_failure(record, failure)
                raise failure
            try:
                await self._interaction.create_session(session)
            except InteractionFailure as interaction_failure:
                code = (
                    AgentLifecycleErrorCode.CLEANUP_FAILED
                    if interaction_failure.code is InteractionErrorCode.CLEANUP_FAILED
                    else AgentLifecycleErrorCode.SESSION_FAILED
                )
                failure = AgentLifecycleFailure(
                    AgentLifecycleOperation.CREATE,
                    code,
                    agent_id=agent_id,
                    state=AgentLifecycleState.FAILED,
                    retryable=interaction_failure.retryable,
                )
                self._save_failure(record, failure)
                raise failure from None
            completed = replace(record, state=AgentLifecycleState.STOPPED, failure=None)
            self._repository.save(completed)
            return CreateAgentResult(completed, True)

    def inspect(self, agent_id: AgentId) -> AgentRecord:
        return self._require(agent_id, AgentLifecycleOperation.INSPECT)

    async def _wait_until_ready(
        self, handle: RuntimeHandle, initial: RuntimeObservation
    ) -> RuntimeObservation:
        observation = initial
        async with asyncio.timeout(self._configuration.readiness_timeout_seconds):
            while True:
                if (
                    observation.execution is ExecutionState.EXECUTING
                    and observation.readiness is Readiness.CONFIRMED
                ):
                    return observation
                if observation.execution is not ExecutionState.EXECUTING:
                    raise AgentLifecycleFailure(
                        AgentLifecycleOperation.START,
                        AgentLifecycleErrorCode.OPERATION_FAILED,
                        agent_id=handle.agent_id,
                        state=AgentLifecycleState.FAILED,
                    )
                await asyncio.sleep(self._configuration.readiness_poll_interval_seconds)
                observation = await self._runtime.status(
                    handle, options=self._configuration.operation_options
                )

    async def start(self, agent_id: AgentId) -> AgentRecord:
        async with self._lock(agent_id):
            record = self._require(agent_id, AgentLifecycleOperation.START)
            if record.conversation_recovery_required:
                raise AgentLifecycleFailure(
                    AgentLifecycleOperation.START,
                    AgentLifecycleErrorCode.SESSION_FAILED,
                    agent_id=agent_id,
                    state=record.state,
                )
            if record.state is AgentLifecycleState.READY:
                return record
            if record.state is not AgentLifecycleState.STOPPED:
                raise AgentLifecycleFailure(
                    AgentLifecycleOperation.START,
                    AgentLifecycleErrorCode.INVALID_STATE,
                    agent_id=agent_id,
                    state=record.state,
                )
            if record.runtime_handle is None:
                raise AgentLifecycleFailure(
                    AgentLifecycleOperation.START,
                    AgentLifecycleErrorCode.INVALID_STATE,
                    agent_id=agent_id,
                    state=record.state,
                )
            starting = replace(record, state=AgentLifecycleState.STARTING, failure=None)
            self._repository.save(starting)
            try:
                initial = await self._runtime.start(
                    record.runtime_handle,
                    options=self._configuration.operation_options,
                )
                starting = replace(starting, runtime_observation=initial)
                self._repository.save(starting)
                observation = await self._wait_until_ready(
                    record.runtime_handle, initial
                )
            except TimeoutError:
                failure = AgentLifecycleFailure(
                    AgentLifecycleOperation.START,
                    AgentLifecycleErrorCode.READINESS_TIMEOUT,
                    agent_id=agent_id,
                    state=AgentLifecycleState.FAILED,
                    retryable=True,
                )
                self._save_failure(starting, failure)
                raise failure from None
            except RuntimeFailure as runtime_failure:
                failure = self._runtime_failure(
                    AgentLifecycleOperation.START, runtime_failure
                )
                self._save_failure(starting, failure, handle=runtime_failure.handle)
                raise failure from None
            except AgentLifecycleFailure as failure:
                self._save_failure(starting, failure)
                raise
            ready = replace(
                starting,
                state=AgentLifecycleState.READY,
                runtime_observation=observation,
            )
            self._repository.save(ready)
            return ready

    async def stop(self, agent_id: AgentId) -> AgentRecord:
        async with self._lock(agent_id):
            record = self._require(agent_id, AgentLifecycleOperation.STOP)
            if record.state is AgentLifecycleState.STOPPED:
                return record
            if record.state not in {
                AgentLifecycleState.READY,
                AgentLifecycleState.FAILED,
            }:
                raise AgentLifecycleFailure(
                    AgentLifecycleOperation.STOP,
                    AgentLifecycleErrorCode.INVALID_STATE,
                    agent_id=agent_id,
                    state=record.state,
                )
            stopping = replace(record, state=AgentLifecycleState.STOPPING, failure=None)
            self._repository.save(stopping)
            observation = record.runtime_observation
            if record.runtime_handle is not None:
                try:
                    observation = await self._runtime.stop(
                        record.runtime_handle,
                        options=self._configuration.operation_options,
                    )
                except RuntimeFailure as runtime_failure:
                    failure = self._runtime_failure(
                        AgentLifecycleOperation.STOP, runtime_failure
                    )
                    self._save_failure(stopping, failure, handle=runtime_failure.handle)
                    raise failure from None
            if (
                observation is not None
                and observation.execution is not ExecutionState.INACTIVE
            ):
                failure = AgentLifecycleFailure(
                    AgentLifecycleOperation.STOP,
                    AgentLifecycleErrorCode.OPERATION_FAILED,
                    agent_id=agent_id,
                    state=AgentLifecycleState.FAILED,
                )
                self._save_failure(stopping, failure, observation=observation)
                raise failure
            stopped = replace(
                stopping,
                state=AgentLifecycleState.STOPPED,
                runtime_observation=observation,
            )
            self._repository.save(stopped)
            return stopped

    async def delete(self, agent_id: AgentId) -> DeleteAgentResult:
        async with self._lock(agent_id):
            record = self._repository.get(agent_id)
            if record is None:
                if self._repository.was_deleted(agent_id):
                    return DeleteAgentResult(agent_id, True)
                raise AgentLifecycleFailure(
                    AgentLifecycleOperation.DELETE,
                    AgentLifecycleErrorCode.NOT_FOUND,
                    agent_id=agent_id,
                )
            if record.state not in {
                AgentLifecycleState.STOPPED,
                AgentLifecycleState.FAILED,
            }:
                raise AgentLifecycleFailure(
                    AgentLifecycleOperation.DELETE,
                    AgentLifecycleErrorCode.INVALID_STATE,
                    agent_id=agent_id,
                    state=record.state,
                )
            if record.runtime_handle is not None:
                try:
                    await self._runtime.delete(
                        record.runtime_handle,
                        options=self._configuration.operation_options,
                    )
                except RuntimeFailure as runtime_failure:
                    failure = self._runtime_failure(
                        AgentLifecycleOperation.DELETE, runtime_failure
                    )
                    self._save_failure(record, failure, handle=runtime_failure.handle)
                    raise failure from None
            try:
                await self._interaction.delete_session(record.session)
            except InteractionFailure as interaction_failure:
                failure = AgentLifecycleFailure(
                    AgentLifecycleOperation.DELETE,
                    AgentLifecycleErrorCode.CLEANUP_FAILED,
                    agent_id=agent_id,
                    state=AgentLifecycleState.FAILED,
                    retryable=interaction_failure.retryable,
                )
                self._save_failure(record, failure)
                raise failure from None
            self._repository.delete(record)
            return DeleteAgentResult(agent_id, False)
