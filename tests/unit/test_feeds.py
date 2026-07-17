"""Unit tests for feeds.py (render, write, verify)."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from paul_graham_essay_feeds.extract import extract_essays
from paul_graham_essay_feeds.feeds import (
    _atomic_write,
    feed_paths,
    render_atom,
    render_json,
    render_rss,
    verify_feed_artifacts,
    write_feeds,
)
from paul_graham_essay_feeds.model import (
    ATOM_NS,
    PROTECTED_PATHS,
    STABLE_UNPUBLISHED_UPDATED,
    Essay,
    FeedError,
    rfc3339,
    stable_updated,
    utc_now,
)
from tests.html_samples import synthetic_index_html


def _essays(*, regular: int = 3) -> list[Essay]:
    """Tiny synthetic catalog for render shape tests (not live inventory size)."""
    floor = regular + len(PROTECTED_PATHS)
    return extract_essays(synthetic_index_html(essay_count=regular), min_items=floor)


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
    assert raw.count("<item>") == len(essays)
    assert "content:encoded" not in raw
    assert "<rss" in raw
    root = ET.fromstring(raw[raw.index("<rss") :])
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"


def test_atom_shape_summary_only() -> None:
    essays = _essays()
    raw = render_atom(essays, built_at=utc_now()).decode()
    assert "<feed" in raw
    assert raw.count("<entry>") == len(essays)
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
    """U6: undated essays omit pub dates; JSON keeps short content_text."""
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
    assert len(data["items"]) == len(essays)
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


def _sample() -> list[Essay]:
    return [
        Essay(
            position=1,
            title="A",
            url="https://paulgraham.com/a.html",
            stable_id="https://paulgraham.com/a.html",
            is_permalink=True,
            summary="Short summary for essay A.",
        ),
        Essay(
            position=2,
            title="B",
            url="https://paulgraham.com/b.html",
            stable_id="https://paulgraham.com/b.html",
            is_permalink=True,
            summary="Short summary for essay B.",
        ),
    ]


def _write_sample(repo_root: Path, essays: list[Essay] | None = None) -> list[Essay]:
    essays = essays if essays is not None else _sample()
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=render_atom(essays, built_at=now),
        json_feed=render_json(essays, built_at=now),
    )
    return essays


def _assert_no_staging_temps(feeds_dir: Path) -> None:
    leftovers = [
        p
        for p in feeds_dir.iterdir()
        if p.is_file()
        and any(p.name.startswith(f".{name}.") for name in ("rss.xml", "atom.xml", "feed.json"))
    ]
    assert leftovers == [], f"leftover staging temps: {[p.name for p in leftovers]}"


def test_write_feeds_creates_expected_paths(repo_root: Path) -> None:
    essays = _sample()
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=render_atom(essays, built_at=now),
        json_feed=render_json(essays, built_at=now),
    )
    paths = feed_paths(repo_root)
    assert paths["rss"].is_file()
    assert paths["atom"].is_file()
    assert paths["json"].is_file()
    assert not (repo_root / "data" / "essays.json").exists()
    assert not (repo_root / "feeds" / ".manifest.json").exists()


def test_write_feeds_happy_path_and_verify(repo_root: Path) -> None:
    essays = _write_sample(repo_root)
    paths = feed_paths(repo_root)
    assert paths["rss"].is_file()
    assert paths["atom"].is_file()
    assert paths["json"].is_file()
    assert not (repo_root / "feeds" / ".manifest.json").exists()

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert len(payload["items"]) == len(essays)
    assert payload["items"][0]["content_text"] == payload["items"][0]["summary"]

    verify_feed_artifacts(repo_root, min_items=2)
    _assert_no_staging_temps(repo_root / "feeds")


def test_write_feeds_overwrites(repo_root: Path) -> None:
    essays = _sample()
    now = utc_now()
    write_feeds(
        repo_root,
        rss=render_rss(essays, built_at=now),
        atom=b"<feed/>",
        json_feed=b"{}",
    )
    write_feeds(
        repo_root,
        rss=render_rss(essays[:1], built_at=now),
        atom=b"<feed/>",
        json_feed=b"{}",
    )
    rss = (repo_root / "feeds" / "rss.xml").read_bytes()
    assert rss.count(b"<item>") == 1
    assert b"<title>A</title>" in rss
    assert b"<title>B</title>" not in rss


def test_feed_paths_keys(repo_root: Path) -> None:
    assert set(feed_paths(repo_root)) == {"rss", "atom", "json"}


def test_atomic_write_cleans_tmp_on_failure(repo_root: Path) -> None:
    target = repo_root / "feeds" / "x.xml"
    with (
        patch("paul_graham_essay_feeds.feeds.os.replace", side_effect=OSError("disk")),
        pytest.raises(OSError, match="disk"),
    ):
        _atomic_write(target, b"data")
    assert list((repo_root / "feeds").glob(".x.xml.*")) == []


@pytest.mark.parametrize("fail_after", [0, 1, 2])
def test_write_feeds_replace_failure_leaves_safe_state(
    repo_root: Path,
    fail_after: int,
) -> None:
    """If os.replace fails mid-publish, temps are cleaned and finals stay whole files."""
    _write_sample(repo_root)
    feeds_dir = repo_root / "feeds"
    prior = {name: (feeds_dir / name).read_bytes() for name in ("rss.xml", "atom.xml", "feed.json")}

    essays = _sample()
    now = utc_now()
    new_rss = render_rss(essays[:1], built_at=now)
    new_atom = render_atom(essays[:1], built_at=now)
    new_json = render_json(essays[:1], built_at=now)
    new_blobs = {"rss.xml": new_rss, "atom.xml": new_atom, "feed.json": new_json}

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if calls["n"] >= fail_after:
            raise OSError("simulated replace failure")
        calls["n"] += 1
        real_replace(src, dst)

    with (
        patch("paul_graham_essay_feeds.feeds.os.replace", side_effect=flaky_replace),
        pytest.raises(OSError, match="simulated replace failure"),
    ):
        write_feeds(
            repo_root,
            rss=new_rss,
            atom=new_atom,
            json_feed=new_json,
        )

    _assert_no_staging_temps(feeds_dir)

    order = ("rss.xml", "atom.xml", "feed.json")
    for i, name in enumerate(order):
        data = (feeds_dir / name).read_bytes()
        # Each final is a complete prior or complete new blob — never truncated.
        if i < fail_after:
            assert data == new_blobs[name]
        else:
            assert data == prior[name]
        assert data  # non-empty whole file


def test_verify_feed_artifacts_missing_content_text(repo_root: Path) -> None:
    _write_sample(repo_root)
    path = feed_paths(repo_root)["json"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["items"][0]["content_text"]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(FeedError, match="content_text"):
        verify_feed_artifacts(repo_root, min_items=2)


def test_verify_feed_artifacts_wrong_content_text(repo_root: Path) -> None:
    _write_sample(repo_root)
    path = feed_paths(repo_root)["json"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["items"][0]["content_text"] = "does not match summary"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(FeedError, match="content_text must equal summary"):
        verify_feed_artifacts(repo_root, min_items=2)


def test_render_json_includes_index_skip_metadata() -> None:
    essays = _sample()
    data = json.loads(
        render_json(
            essays,
            built_at=utc_now(),
            index_hash="abc123",
            index_fingerprint="fp-line",
        )
    )
    meta = data["_pg_essay_feeds"]
    assert meta["index_hash"] == "abc123"
    assert meta["index_fingerprint"] == "fp-line"
    assert meta["item_count"] == 2
