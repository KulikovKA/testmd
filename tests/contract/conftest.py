"""Replace runtime_harness for a real driver; keep the contract tests unchanged."""

import pytest

from tests.runtime_support.fake_runtime import FakeRuntime
from tests.runtime_support.harness import RuntimeHarness


@pytest.fixture
def runtime_harness() -> RuntimeHarness:
    # The scenario wrapper closes the harness in the operation event loop.
    return FakeRuntime()
