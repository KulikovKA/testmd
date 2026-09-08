"""Scaffold-level tests."""

from universal_agent_runtime import create_composition


def test_composition_entry_point_is_importable_and_inert() -> None:
    composition = create_composition()

    assert composition.package_name == "universal_agent_runtime"
