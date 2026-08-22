"""RV-R-002: Atom feed ids via FeedSnapshot.variant, not URL substring."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Literal

from paul_graham_essay_feeds.feeds import render_atom
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    FEED_ID,
    FEED_ID_SIMPLE,
    FeedEntrySnapshot,
    FeedSnapshot,
)

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
Variant = Literal["enriched", "simple"]


def _snap(*, variant: Variant, feed_url: str | None = None) -> FeedSnapshot:
    return FeedSnapshot(
        logical_updated_at=T0,
        generator="pg-essay-feeds/test",
        feed_url=feed_url,
        variant=variant,
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="A",
                summary="Short summary for essay A.",
                observed_updated_at=T0,
            )
        ],
    )


def _atom_id(blob: bytes) -> str:
    root = ET.fromstring(blob)
    el = root.find(f"{{{ATOM_NS}}}id")
    assert el is not None and el.text
    return el.text


def test_enriched_variant_atom_id() -> None:
    assert _atom_id(render_atom(_snap(variant="enriched"))) == FEED_ID


def test_simple_variant_atom_id_without_feed_url() -> None:
    assert _atom_id(render_atom(_snap(variant="simple", feed_url=None))) == FEED_ID_SIMPLE


def test_simple_substring_in_url_does_not_force_simple_id() -> None:
    """Base URL containing 'simple' must not flip enriched feed id."""
    blob = render_atom(
        _snap(
            variant="enriched",
            feed_url="https://example.com/simple-hosting/feed.json",
        )
    )
    assert _atom_id(blob) == FEED_ID


def test_committed_simple_atom_uses_feed_id_simple() -> None:
    """Published feeds/atom.simple.xml must not reuse the enriched feed id."""
    from pathlib import Path

    blob = Path(__file__).resolve().parents[3] / "feeds" / "atom.simple.xml"
    assert _atom_id(blob.read_bytes()) == FEED_ID_SIMPLE
