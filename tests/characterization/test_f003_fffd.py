"""F-003: committed feed data must not contain U+FFFD."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.characterization
@pytest.mark.xfail(strict=True, reason="F-003: committed feed.json still contains U+FFFD")
def test_committed_feed_json_has_no_replacement_char() -> None:
    text = (ROOT / "feeds" / "feed.json").read_text(encoding="utf-8")
    assert "\ufffd" not in text, "U+FFFD present in committed feed.json"
