"""T-C006 / RV-C-006: durable CatalogEntry summaries are schema-capped."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.catalog import load_catalog
from paul_graham_essay_feeds.models import FEED_SUMMARY_CHARS, CatalogEntry, Essay

_REPO = Path(__file__).resolve().parents[3]
_SID = "https://paulgraham.com/a.html"


def _payload(**fields: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "stable_id": _SID,
        "url": _SID,
        "title": "A",
        "position": 0,
    }
    payload.update(fields)
    return payload


@pytest.mark.characterization
@pytest.mark.parametrize("field", ["summary", "prior_good_summary"])
def test_catalog_entry_summary_fields_accept_cap(field: str) -> None:
    text = "x" * FEED_SUMMARY_CHARS
    entry = CatalogEntry.model_validate(_payload(**{field: text}))
    assert getattr(entry, field) == text


@pytest.mark.characterization
@pytest.mark.parametrize("field", ["summary", "prior_good_summary"])
def test_catalog_entry_summary_fields_reject_oversize(field: str) -> None:
    with pytest.raises(ValidationError, match="at most"):
        CatalogEntry.model_validate(_payload(**{field: "x" * (FEED_SUMMARY_CHARS + 1)}))


@pytest.mark.characterization
def test_catalog_entry_accepts_399_char_summaries() -> None:
    """Committed catalog max is 399; the 400-char cap must not reject that."""
    text = "x" * 399
    entry = CatalogEntry.model_validate(_payload(summary=text, prior_good_summary=text))
    assert entry.summary == text
    assert entry.prior_good_summary == text


@pytest.mark.characterization
def test_committed_catalog_loads_under_summary_cap() -> None:
    catalog = load_catalog(_REPO / "catalog.json")
    assert catalog is not None
    lengths: list[int] = []
    for entry in catalog.entries.values():
        for value in (entry.summary, entry.prior_good_summary):
            if value is None:
                continue
            assert len(value) <= FEED_SUMMARY_CHARS
            lengths.append(len(value))
    assert lengths
    assert max(lengths) <= FEED_SUMMARY_CHARS


@pytest.mark.characterization
def test_essay_summary_is_not_schema_capped() -> None:
    """Truncation stays in feed_summary(); Essay.summary must still accept long strings."""
    long_summary = "S" * (FEED_SUMMARY_CHARS + 100)
    essay = Essay(
        position=1,
        title="T",
        url=_SID,
        stable_id=_SID,
        is_permalink=True,
        summary=long_summary,
    )
    assert essay.summary == long_summary
