"""Driver conformance, expressed only through the neutral port and test harness."""

import asyncio
from dataclasses import replace
from uuid import UUID

import pytest

from tests.runtime_support.harness import Phase, RuntimeHarness, scenario
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
    EnvironmentVariable,
    NetworkDestination,
    Readiness,
    ResourceLimits,
    RuntimeHandle,
    SecretBinding,
)
from universal_agent_runtime.application.ports.runtime_values import (
    ExecutionState as State,
)
from universal_agent_runtime.domain.identifiers import AgentId


@scenario
async def test_lifecycle_readiness_retention_and_delete(
    runtime_harness: RuntimeHarness,
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    created = await driver.create(request, options=runtime_harness.options)
    assert created.handle.agent_id == request.agent_id
    assert created.workspace_id == request.workspace_id
    assert created.execution is State.INACTIVE
    assert created.readiness is Readiness.UNCONFIRMED
    started = await driver.start(created.handle, options=runtime_harness.options)
    assert started.execution is State.EXECUTING
    assert started.readiness is Readiness.UNCONFIRMED
    await runtime_harness.confirm_ready(created.handle)
    assert (
        await driver.status(created.handle, options=runtime_harness.options)
    ).readiness is Readiness.CONFIRMED
    await runtime_harness.write_workspace(created.handle, "synthetic retained content")
    stopped = await driver.stop(created.handle, options=runtime_harness.options)
    assert stopped.execution is State.INACTIVE
    assert stopped.readiness is Readiness.UNCONFIRMED
    restarted = await driver.start(created.handle, options=runtime_harness.options)
    assert restarted.readiness is Readiness.UNCONFIRMED
    assert (
        await runtime_harness.read_workspace(created.handle)
        == "synthetic retained content"
    )
    await driver.stop(created.handle, options=runtime_harness.options)
    result = await driver.delete(created.handle, options=runtime_harness.options)
    assert result.handle == created.handle
    assert await runtime_harness.resource_count() == 0
    assert (
        await driver.delete(created.handle, options=runtime_harness.options) == result
    )
    with pytest.raises(RuntimeFailure) as error:
        await driver.status(created.handle, options=runtime_harness.options)
    assert error.value.code is Code.NOT_FOUND
    with pytest.raises(RuntimeFailure) as error:
        await driver.create(request, options=runtime_harness.options)
    assert error.value.code is Code.CONFLICT  # A late retry cannot resurrect an Agent.


@scenario
async def test_retries_and_concurrent_calls_do_not_duplicate_or_reset(
    runtime_harness: RuntimeHarness,
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    created = await asyncio.gather(
        *(driver.create(request, options=runtime_harness.options) for _ in range(4))
    )
    assert len({observation.handle for observation in created}) == 1
    assert (
        await runtime_harness.resource_count() == runtime_harness.resources_per_runtime
    )
    handle = created[0].handle
    await asyncio.gather(
        *(driver.start(handle, options=runtime_harness.options) for _ in range(4))
    )
    await runtime_harness.confirm_ready(handle)
    assert (
        await driver.start(handle, options=runtime_harness.options)
    ).readiness is Readiness.CONFIRMED
    assert (
        await driver.create(request, options=runtime_harness.options)
    ).readiness is Readiness.CONFIRMED
    assert (
        await driver.status(handle, options=runtime_harness.options)
    ).execution is State.EXECUTING
    await asyncio.gather(
        *(driver.stop(handle, options=runtime_harness.options) for _ in range(4))
    )
    assert (
        await driver.status(handle, options=runtime_harness.options)
    ).execution is State.INACTIVE
    await asyncio.gather(
        *(driver.delete(handle, options=runtime_harness.options) for _ in range(4))
    )
    assert await runtime_harness.resource_count() == 0


@scenario
async def test_conflicting_create_and_shared_workspace_are_rejected(
    runtime_harness: RuntimeHarness,
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    created = await driver.create(request, options=runtime_harness.options)
    for changed in (
        replace(request, workload="different-workload"),
        replace(request, agent_id=AgentId("another-agent")),
        replace(request, workspace_id=runtime_harness.request("two").workspace_id),
        replace(
            request,
            resources=ResourceLimits(
                request.resources.cpu_cores + 1, request.resources.memory_bytes
            ),
        ),
        replace(request, environment=(EnvironmentVariable("TEST_VALUE", "changed"),)),
        replace(request, secrets=(SecretBinding("TEST_SECRET", "changed-ref"),)),
        replace(request, network=(NetworkDestination("example.invalid", 443),)),
    ):
        with pytest.raises(RuntimeFailure) as error:
            await driver.create(changed, options=runtime_harness.options)
        assert error.value.code is Code.CONFLICT
    assert (
        await driver.status(created.handle, options=runtime_harness.options) == created
    )
    assert (
        await runtime_harness.resource_count() == runtime_harness.resources_per_runtime
    )


@scenario
async def test_separate_agents_keep_distinct_resources(
    runtime_harness: RuntimeHarness,
) -> None:
    driver = runtime_harness.runtime
    first, second = await asyncio.gather(
        driver.create(runtime_harness.request("one"), options=runtime_harness.options),
        driver.create(runtime_harness.request("two"), options=runtime_harness.options),
    )
    assert first.handle != second.handle
    assert first.workspace_id != second.workspace_id
    await runtime_harness.write_workspace(first.handle, "first")
    await runtime_harness.write_workspace(second.handle, "second")
    await driver.delete(first.handle, options=runtime_harness.options)
    assert await runtime_harness.read_workspace(second.handle) == "second"
    assert await driver.status(second.handle, options=runtime_harness.options) == second
    assert (
        await runtime_harness.resource_count() == runtime_harness.resources_per_runtime
    )


@pytest.mark.parametrize("operation", [Op.START, Op.STATUS, Op.STOP, Op.DELETE])
@scenario
async def test_unknown_handles_and_owner_mismatch(
    runtime_harness: RuntimeHarness, operation: Op
) -> None:
    driver = runtime_harness.runtime
    method = getattr(driver, operation.value)
    unknown = RuntimeHandle(AgentId("unknown"), UUID(int=2**128 - 1))
    if operation is Op.DELETE:
        assert (
            await method(unknown, options=runtime_harness.options)
        ).handle == unknown
    else:
        with pytest.raises(RuntimeFailure) as error:
            await method(unknown, options=runtime_harness.options)
        assert error.value.code is Code.NOT_FOUND
    created = await driver.create(
        runtime_harness.request(), options=runtime_harness.options
    )
    forged = replace(created.handle, agent_id=AgentId("another-agent"))
    with pytest.raises(RuntimeFailure) as error:
        await method(forged, options=runtime_harness.options)
    assert error.value.code is Code.NOT_FOUND
    assert error.value.agent_id == forged.agent_id
    assert error.value.handle is None
    assert (
        await driver.status(created.handle, options=runtime_harness.options) == created
    )


@pytest.mark.parametrize(
    "state,operation",
    [
        (State.EXECUTING, Op.DELETE),
        (State.UNKNOWN, Op.DELETE),
        (State.UNKNOWN, Op.START),
        (State.FAULTED, Op.START),
    ],
)
@scenario
async def test_invalid_state_is_non_mutating(
    runtime_harness: RuntimeHarness, state: State, operation: Op
) -> None:
    driver = runtime_harness.runtime
    created = await driver.create(
        runtime_harness.request(), options=runtime_harness.options
    )
    await runtime_harness.force_state(created.handle, state)
    before = await driver.status(created.handle, options=runtime_harness.options)
    with pytest.raises(RuntimeFailure) as error:
        await getattr(driver, operation.value)(
            created.handle, options=runtime_harness.options
        )
    assert error.value.code is Code.INVALID_STATE
    assert (
        await driver.status(created.handle, options=runtime_harness.options) == before
    )


@pytest.mark.parametrize("state", [State.UNKNOWN, State.FAULTED])
@scenario
async def test_stop_reconciles_uncertain_or_failed_execution(
    runtime_harness: RuntimeHarness, state: State
) -> None:
    driver = runtime_harness.runtime
    created = await driver.create(
        runtime_harness.request(), options=runtime_harness.options
    )
    await runtime_harness.force_state(created.handle, state)
    stopped = await driver.stop(created.handle, options=runtime_harness.options)
    assert stopped.execution is State.INACTIVE
    assert stopped.readiness is Readiness.UNCONFIRMED
    assert (
        await driver.start(created.handle, options=runtime_harness.options)
    ).execution is State.EXECUTING


@scenario
async def test_partial_create_rolls_back_and_can_retry(
    runtime_harness: RuntimeHarness,
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    runtime_harness.fail_next(Op.CREATE, Phase.ALLOCATED, Code.OPERATION_FAILED)
    with pytest.raises(RuntimeFailure) as error:
        await driver.create(request, options=runtime_harness.options)
    assert error.value.code is Code.OPERATION_FAILED
    assert error.value.operation is Op.CREATE
    assert await runtime_harness.resource_count() == 0
    assert (
        await driver.create(request, options=runtime_harness.options)
    ).execution is State.INACTIVE
    assert (
        await runtime_harness.resource_count() == runtime_harness.resources_per_runtime
    )


@scenario
async def test_partial_create_cleanup_failure_retains_recovery_handle(
    runtime_harness: RuntimeHarness,
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    runtime_harness.fail_next(Op.CREATE, Phase.ALLOCATED, Code.OPERATION_FAILED)
    runtime_harness.fail_cleanup_once()
    with pytest.raises(RuntimeFailure) as error:
        await driver.create(request, options=runtime_harness.options)
    assert error.value.code is Code.CLEANUP_FAILED
    handle = error.value.handle
    assert handle is not None
    assert (
        await driver.status(handle, options=runtime_harness.options)
    ).execution is State.FAULTED
    assert await runtime_harness.resource_count() > 0
    with pytest.raises(RuntimeFailure) as retried:
        await driver.create(request, options=runtime_harness.options)
    assert retried.value.code is Code.CLEANUP_FAILED
    assert retried.value.handle == handle
    for operation in (Op.START, Op.STOP):
        with pytest.raises(RuntimeFailure) as invalid:
            await getattr(driver, operation.value)(
                handle, options=runtime_harness.options
            )
        assert invalid.value.code is Code.INVALID_STATE
    await driver.delete(handle, options=runtime_harness.options)
    assert await runtime_harness.resource_count() == 0


@scenario
async def test_delete_failure_is_not_reported_as_success(
    runtime_harness: RuntimeHarness,
) -> None:
    driver = runtime_harness.runtime
    created = await driver.create(
        runtime_harness.request(), options=runtime_harness.options
    )
    runtime_harness.fail_cleanup_once()
    with pytest.raises(RuntimeFailure) as error:
        await driver.delete(created.handle, options=runtime_harness.options)
    assert error.value.code is Code.CLEANUP_FAILED
    assert error.value.handle == created.handle
    assert await runtime_harness.resource_count() > 0
    assert (
        await driver.status(created.handle, options=runtime_harness.options)
    ).execution is State.FAULTED
    for operation in (Op.START, Op.STOP):
        with pytest.raises(RuntimeFailure) as invalid:
            await getattr(driver, operation.value)(
                created.handle, options=runtime_harness.options
            )
        assert invalid.value.code is Code.INVALID_STATE
    with pytest.raises(RuntimeFailure) as replay:
        await driver.create(runtime_harness.request(), options=runtime_harness.options)
    assert replay.value.code is Code.CLEANUP_FAILED
    assert replay.value.handle == created.handle
    await driver.delete(created.handle, options=runtime_harness.options)
    assert await runtime_harness.resource_count() == 0


@pytest.mark.parametrize("operation", list(Op))
@scenario
async def test_unavailable_before_operation_preserves_resources(
    runtime_harness: RuntimeHarness, operation: Op
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    created = await driver.create(request, options=runtime_harness.options)
    runtime_harness.fail_next(operation, Phase.BEFORE, Code.UNAVAILABLE)
    argument = request if operation is Op.CREATE else created.handle
    with pytest.raises(RuntimeFailure) as error:
        await getattr(driver, operation.value)(
            argument, options=runtime_harness.options
        )
    assert error.value.code is Code.UNAVAILABLE
    assert error.value.retryable
    assert (
        await driver.status(created.handle, options=runtime_harness.options) == created
    )
    assert (
        await runtime_harness.resource_count() == runtime_harness.resources_per_runtime
    )


@pytest.mark.parametrize("operation", [Op.CREATE, Op.START, Op.STOP, Op.DELETE])
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
@scenario
async def test_lost_response_after_commit_can_be_reconciled(
    runtime_harness: RuntimeHarness, operation: Op, interruption: str
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    created = (
        None
        if operation is Op.CREATE
        else await driver.create(request, options=runtime_harness.options)
    )
    if operation is Op.STOP:
        assert created is not None
        await driver.start(created.handle, options=runtime_harness.options)
    pause = runtime_harness.pause_next(operation, Phase.COMMITTED)
    argument = request if created is None else created.handle
    call_options = (
        runtime_harness.interruption_options
        if interruption == "timeout"
        else runtime_harness.options
    )
    task = asyncio.create_task(
        getattr(driver, operation.value)(argument, options=call_options)
    )
    await pause.entered.wait()
    if interruption == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeFailure) as error:
            await task
        assert error.value.code is Code.TIMEOUT
        assert error.value.retryable
    result = await getattr(driver, operation.value)(
        argument, options=runtime_harness.options
    )
    if operation is Op.DELETE:
        assert await runtime_harness.resource_count() == 0
    else:
        assert (
            await runtime_harness.resource_count()
            == runtime_harness.resources_per_runtime
        )
        assert result.execution is (
            State.EXECUTING if operation is Op.START else State.INACTIVE
        )


@pytest.mark.parametrize("cleanup_fails", [False, True])
@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
@scenario
async def test_interrupted_partial_create_never_loses_resource_ownership(
    runtime_harness: RuntimeHarness, cleanup_fails: bool, interruption: str
) -> None:
    driver = runtime_harness.runtime
    request = runtime_harness.request()
    pause = runtime_harness.pause_next(Op.CREATE, Phase.ALLOCATED)
    if cleanup_fails:
        runtime_harness.fail_cleanup_once()
    call_options = (
        runtime_harness.interruption_options
        if interruption == "timeout"
        else runtime_harness.options
    )
    task = asyncio.create_task(driver.create(request, options=call_options))
    await pause.entered.wait()
    if interruption == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeFailure) as error:
            await task
        assert error.value.code is Code.TIMEOUT
    if cleanup_fails:
        with pytest.raises(RuntimeFailure) as error:
            await driver.create(request, options=runtime_harness.options)
        assert error.value.code is Code.CLEANUP_FAILED
        assert error.value.handle is not None
        await driver.delete(error.value.handle, options=runtime_harness.options)
        assert await runtime_harness.resource_count() == 0
    else:
        assert await runtime_harness.resource_count() == 0
        await driver.create(request, options=runtime_harness.options)
        assert (
            await runtime_harness.resource_count()
            == runtime_harness.resources_per_runtime
        )


@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
@scenario
async def test_admission_wait_is_bounded_and_does_not_mutate(
    runtime_harness: RuntimeHarness, interruption: str
) -> None:
    driver = runtime_harness.runtime
    created = await driver.create(
        runtime_harness.request(), options=runtime_harness.options
    )
    pause = runtime_harness.pause_next(Op.START, Phase.BEFORE)
    first = asyncio.create_task(
        driver.start(created.handle, options=runtime_harness.options)
    )
    await pause.entered.wait()
    admitted = asyncio.Event()

    async def waiting_stop() -> None:
        admitted.set()
        await driver.stop(
            created.handle,
            options=runtime_harness.interruption_options
            if interruption == "timeout"
            else runtime_harness.options,
        )

    second = asyncio.create_task(waiting_stop())
    await admitted.wait()
    if interruption == "cancel":
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
    else:
        with pytest.raises(RuntimeFailure) as error:
            await second
        assert error.value.code is Code.TIMEOUT
    pause.release.set()
    await first
    assert (
        await driver.status(created.handle, options=runtime_harness.options)
    ).execution is State.EXECUTING
