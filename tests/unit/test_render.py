"""Unit tests for render.py."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

from paul_graham_essay_feeds.extract import extract_essays
from paul_graham_essay_feeds.feeds import render_atom, render_json, render_rss
from paul_graham_essay_feeds.model import (
    ATOM_NS,
    STABLE_UNPUBLISHED_UPDATED,
    Essay,
    rfc3339,
    stable_updated,
    utc_now,
)
from tests.html_samples import synthetic_index_html


def _essays() -> list[Essay]:
    return extract_essays(synthetic_index_html(), min_items=233)


def _undated_essay(*, summary: str = "Short undated summary.") -> Essay:
    return Essay(
        position=1,
        title="Undated",
        url="https://paulgraham.com/undated.html",
        stable_id="https://paulgraham.com/undated.html",
        is_permalink=True,
        summary=summary,
        published_at=None,
    )


def _atom_entry_updateds(raw: str) -> list[str]:
    root = ET.fromstring(raw[raw.index("<feed") :])
    return [el.text or "" for el in root.findall(f"{{{ATOM_NS}}}entry/{{{ATOM_NS}}}updated")]


def test_rss_shape_no_full_content() -> None:
    essays = _essays()
    raw = render_rss(essays, built_at=utc_now()).decode()
    assert raw.startswith("<?xml")
    assert raw.count("<item>") == 233
    assert "content:encoded" not in raw
    assert "<rss" in raw
    root = ET.fromstring(raw[raw.index("<rss") :])
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"


def test_atom_shape_summary_only() -> None:
    essays = _essays()
    raw = render_atom(essays, built_at=utc_now()).decode()
    assert "<feed" in raw
    assert raw.count("<entry>") == 233
    assert f'xmlns="{ATOM_NS}"' in raw
    assert "<content" not in raw
    assert "<summary" in raw


def test_atom_undated_entry_updated_stable_across_built_at() -> None:
    undated = _undated_essay()
    t1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 7, 17, 18, 30, 0, tzinfo=UTC)
    raw1 = render_atom([undated], built_at=t1).decode()
    raw2 = render_atom([undated], built_at=t2).decode()
    expected = rfc3339(stable_updated(undated.stable_id))
    assert expected == rfc3339(STABLE_UNPUBLISHED_UPDATED)
    assert _atom_entry_updateds(raw1) == _atom_entry_updateds(raw2)
    assert _atom_entry_updateds(raw1) == [expected]
    # Feed-level updated may differ with built_at.
    feed_updated = re.findall(r"<feed[^>]*>.*?<updated>([^<]+)</updated>", raw1, re.S)
    feed_updated2 = re.findall(r"<feed[^>]*>.*?<updated>([^<]+)</updated>", raw2, re.S)
    assert feed_updated[0] == rfc3339(t1)
    assert feed_updated2[0] == rfc3339(t2)
    assert "<published>" not in raw1
    assert "<published>" not in raw2


def test_undated_omits_publish_dates_keeps_json_content_text() -> None:
    """U6: published_at=None → omit pubDate / published / date_published; JSON keeps short content_text."""
    undated = _undated_essay(summary="Metadata-only summary for undated essay.")
    now = utc_now()
    rss = render_rss([undated], built_at=now).decode()
    atom = render_atom([undated], built_at=now).decode()
    item = json.loads(render_json([undated], built_at=now))["items"][0]

    assert "<pubDate>" not in rss
    assert "<published>" not in atom
    assert _atom_entry_updateds(atom) == [rfc3339(stable_updated(undated.stable_id))]
    assert "date_published" not in item
    assert "content_text" in item
    assert item["content_text"] == item["summary"] == undated.feed_summary()
    assert item["content_text"] == "Metadata-only summary for undated essay."


def test_json_feed_shape_short_content_text() -> None:
    essays = _essays()
    data = json.loads(render_json(essays, built_at=utc_now()))
    assert data["version"] == "https://jsonfeed.org/version/1.1"
    assert len(data["items"]) == 233
    assert data["items"][0]["id"] == essays[0].stable_id
    assert data["items"][0]["url"] == essays[0].url
    item0 = data["items"][0]
    assert "content_text" in item0
    assert item0["summary"] == essays[0].feed_summary()
    assert item0["content_text"] == item0["summary"] == essays[0].feed_summary()
    assert "authors" in item0


def test_cross_format_id_parity() -> None:
    essays = _essays()
    now = utc_now()
    rss = render_rss(essays, built_at=now).decode()
    atom = render_atom(essays, built_at=now).decode()
    data = json.loads(render_json(essays, built_at=now))
    assert essays[0].stable_id in rss
    assert essays[0].stable_id in atom
    assert data["items"][0]["id"] == essays[0].stable_id


def test_render_uses_enriched_summary() -> None:
    long_body = "Full body text should not appear in feeds. " * 40
    e = Essay(
        position=1,
        title="T",
        url="https://paulgraham.com/t.html",
        stable_id="https://paulgraham.com/t.html",
        is_permalink=True,
        summary="Real scraped summary about startups.",
        content_text=long_body,
        published_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    now = utc_now()
    rss = render_rss([e], built_at=now).decode()
    atom = render_atom([e], built_at=now).decode()
    data = json.loads(render_json([e], built_at=now))
    assert "Real scraped summary" in rss
    assert "Full body text" not in rss
    assert "content:encoded" not in rss
    assert "Real scraped summary" in atom
    assert "<content" not in atom
    item = data["items"][0]
    assert item["summary"] == "Real scraped summary about startups."
    assert item["content_text"] == item["summary"] == e.feed_summary()
    assert item["content_text"] != e.content_text
    assert "date_published" in item
