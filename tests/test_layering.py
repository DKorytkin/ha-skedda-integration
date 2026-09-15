"""The layering rules from the design spec, enforced rather than trusted."""

from __future__ import annotations

import ast
from pathlib import Path

COMPONENT = Path("custom_components/skedda_scheduler")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _offenders(package: str, forbidden: tuple[str, ...]) -> list[str]:
    bad: list[str] = []
    for path in sorted((COMPONENT / package).rglob("*.py")):
        for name in _imported_modules(path):
            if any(name == f or name.startswith(f"{f}.") for f in forbidden):
                bad.append(f"{path}: {name}")
    return bad


def test_core_imports_neither_home_assistant_nor_aiohttp() -> None:
    assert _offenders("core", ("homeassistant", "aiohttp")) == []


def test_api_imports_neither_core_nor_home_assistant() -> None:
    forbidden = ("homeassistant", "custom_components.skedda_scheduler.core")
    assert _offenders("api", forbidden) == []
