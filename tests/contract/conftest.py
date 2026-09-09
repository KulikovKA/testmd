"""Replace runtime_harness for a real driver; keep the contract tests unchanged."""

import pytest

from tests.runtime_support.docker_runtime import (
    DockerRuntimeHarness,
    build_test_image,
    docker_available,
)
from tests.runtime_support.fake_runtime import FakeRuntime
from tests.runtime_support.harness import RuntimeHarness


@pytest.fixture(params=["fake", "docker"])
def runtime_harness(request: pytest.FixtureRequest) -> RuntimeHarness:
    # The scenario wrapper closes the harness in the operation event loop.
    if request.param == "fake":
        return FakeRuntime()
    if not docker_available():
        pytest.skip("local Docker daemon is unavailable")
    build_test_image()
    return DockerRuntimeHarness()
