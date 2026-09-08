"""Checks specific to the deterministic double, outside the shared contract."""

from dataclasses import replace

import pytest

from tests.runtime_support.fake_runtime import FakeRuntime
from tests.runtime_support.harness import scenario
from universal_agent_runtime.application.ports.runtime_errors import (
    RuntimeErrorCode,
    RuntimeFailure,
)
from universal_agent_runtime.application.ports.runtime_values import OperationOptions


@scenario
async def test_unknown_workload_fails_closed_without_allocating() -> None:
    driver = FakeRuntime()
    with pytest.raises(RuntimeFailure) as error:
        await driver.create(
            replace(driver.request(), workload="not-configured"),
            options=OperationOptions(1),
        )
    assert error.value.code is RuntimeErrorCode.CONFIGURATION_REJECTED
    assert await driver.resource_count() == 0


@scenario
async def test_instances_have_no_shared_mutable_state() -> None:
    first, second = FakeRuntime(), FakeRuntime()
    created = await first.create(first.request(), options=OperationOptions(1))
    assert await first.resource_count() == 2
    assert await second.resource_count() == 0
    await first.write_workspace(created.handle, "first-only")
    other = await second.create(second.request(), options=OperationOptions(1))
    assert await second.read_workspace(other.handle) == ""
