"""Translations must stay in step with strings.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

COMPONENT = Path("custom_components/skedda_scheduler")
LANGUAGES = ["en", "uk"]


def leaf_keys(node: Any, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    found: set[str] = set()
    for key, value in node.items():
        found |= leaf_keys(value, f"{prefix}.{key}" if prefix else key)
    return found


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_translation_has_every_key_from_strings_json(language: str) -> None:
    source = load(COMPONENT / "strings.json")
    translated = load(COMPONENT / "translations" / f"{language}.json")

    missing = leaf_keys(source) - leaf_keys(translated)

    assert not missing, f"{language}.json is missing: {sorted(missing)}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_translation_has_no_keys_strings_json_lacks(language: str) -> None:
    """A stale key is a string nobody will ever see, hiding a real omission."""
    source = load(COMPONENT / "strings.json")
    translated = load(COMPONENT / "translations" / f"{language}.json")

    extra = leaf_keys(translated) - leaf_keys(source)

    assert not extra, f"{language}.json has stale keys: {sorted(extra)}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_placeholder_survives_translation(language: str) -> None:
    """A dropped {placeholder} turns a helpful message into a mystery."""
    import re

    source = load(COMPONENT / "strings.json")
    translated = load(COMPONENT / "translations" / f"{language}.json")

    translated_strings = _all_strings(translated)
    for key, text in _all_strings(source).items():
        expected = set(re.findall(r"\{(\w+)\}", text))
        actual = set(re.findall(r"\{(\w+)\}", translated_strings[key]))
        assert expected == actual, f"{language}.json {key}: expected {expected}, got {actual}"


def test_english_is_a_copy_of_the_source_strings() -> None:
    """Home Assistant loads translations/, not strings.json, for a custom component."""
    assert load(COMPONENT / "strings.json") == load(COMPONENT / "translations" / "en.json")


@pytest.mark.parametrize("language", [*LANGUAGES, "source"])
def test_no_translated_string_carries_a_url(language: str) -> None:
    """Home Assistant rejects URLs in translations; hassfest fails the build.

    Catching it here costs a second, rather than a round trip through CI.
    """
    import re

    path = (
        COMPONENT / "strings.json"
        if language == "source"
        else COMPONENT / "translations" / f"{language}.json"
    )

    offenders = [
        key for key, text in _all_strings(load(path)).items() if re.search(r"https?://", text)
    ]

    assert not offenders, f"{path.name} carries URLs at: {offenders}"


def _all_strings(node: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(node, str):
        return {prefix: node}
    found: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            found |= _all_strings(value, f"{prefix}.{key}" if prefix else key)
    return found
