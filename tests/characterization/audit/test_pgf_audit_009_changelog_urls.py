"""PGF-AUDIT-009: CHANGELOG footer compare URLs use existing tags only."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CHANGELOG = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
_FOOTER_RE = re.compile(r"^\[[^\]]+\]:\s+\S+")
_TAG_RE = re.compile(r"v\d+\.\d+\.\d+")
_KNOWN_TAGS = frozenset({"v1.0.1", "v1.0.0", "v0.1.0"})


def _footer_lines() -> list[str]:
    return [line for line in _CHANGELOG.splitlines() if _FOOTER_RE.match(line)]


def test_changelog_footer_compare_urls_use_existing_tags() -> None:
    footer = _footer_lines()
    compare_urls = [line for line in footer if "compare/" in line]
    for line in compare_urls:
        assert "v0.2.0" not in line
    unreleased = next(line for line in footer if line.startswith("[Unreleased]:"))
    assert unreleased.endswith("compare/v1.0.1...HEAD")
    v101 = next(line for line in footer if line.startswith("[1.0.1]:"))
    assert "compare/v1.0.0...v1.0.1" in v101
    found: set[str] = set()
    for line in footer:
        _label, _, url = line.partition("]: ")
        found.update(_TAG_RE.findall(url))
    assert found == _KNOWN_TAGS
