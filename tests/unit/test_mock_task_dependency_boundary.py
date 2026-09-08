"""Keep the standalone mock service independent of runtime/product components."""

import ast
from pathlib import Path


def test_mock_service_has_no_product_or_infrastructure_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "mock_task_service"
    forbidden = ("universal_agent_runtime", "docker", "qwen", "ollama")
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert not any(module.startswith(forbidden) for module in imports)
