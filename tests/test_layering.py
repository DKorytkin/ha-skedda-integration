"""The layering rules from the design spec, enforced rather than trusted."""

from __future__ import annotations

import ast
from pathlib import Path

COMPONENT = Path("custom_components/skedda_scheduler")


def _package_of(path: Path) -> list[str]:
    """The package a module lives in, e.g. custom_components.skedda_scheduler.api."""
    parts = list(path.parts)
    return parts[parts.index("custom_components") : -1]


def _imported_modules(path: Path) -> set[str]:
    """Every module a file imports, with relative imports resolved.

    Resolving them matters: `from ..core import x` inside api/ is exactly the
    dependency this file exists to forbid, and it carries no dotted name of its
    own to match against.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    names.add(node.module)
                continue
            package = _package_of(path)
            base = package[: len(package) - node.level + 1]
            names.add(".".join([*base, node.module] if node.module else base))
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


def test_the_detector_resolves_relative_imports(tmp_path: Path) -> None:
    """Otherwise `from ..core import x` inside api/ would pass unnoticed.

    The rule is only worth as much as the detector behind it, and every import
    in the integration itself is relative.
    """
    offender = tmp_path / "custom_components" / "skedda_scheduler" / "api" / "sneaky.py"
    offender.parent.mkdir(parents=True)
    offender.write_text("from ..core.provider import Space\nfrom .errors import X\n")

    resolved = _imported_modules(offender)

    assert "custom_components.skedda_scheduler.core.provider" in resolved
    assert "custom_components.skedda_scheduler.api.errors" in resolved
