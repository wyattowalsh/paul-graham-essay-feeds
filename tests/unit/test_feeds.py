"""Unit tests for feeds.py (snapshot-native render, write, verify, projection)."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.feeds import (
    catalog_to_feed_snapshot,
    feed_paths,
    render_atom,
    render_json,
    render_rss,
    render_snapshot_feeds,
    verify_feed_artifacts,
    write_feeds,
)
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    FEED_SUMMARY_CHARS,
    Catalog,
    CatalogEntry,
    FeedEntrySnapshot,
    FeedError,
    FeedSnapshot,
    ResourceState,
    blurb,
    rfc3339,
)

GENERATOR = "pg-essay-feeds/0.1.0"
T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2024, 6, 15, 9, 30, 0, tzinfo=UTC)
T2 = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
_OBSERVED = datetime(2024, 3, 1, 8, 0, 0, tzinfo=UTC)
_LOGICAL = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _entry_snap(
    *,
    sid: str = "https://paulgraham.com/a.html",
    title: str = "A",
    summary: str = "Short summary for essay A.",
    observed: datetime = _OBSERVED,
    published_at: datetime | None = None,
    url: str | None = None,
) -> FeedEntrySnapshot:
    return FeedEntrySnapshot(
        id=sid,
        url=url or sid,
        title=title,
        summary=summary,
        observed_updated_at=observed,
        published_at=published_at,
    )


def _snapshot(
    items: list[FeedEntrySnapshot] | None = None,
    *,
    logical_updated_at: datetime = _LOGICAL,
    index_hash: str | None = None,
    index_fingerprint: str | None = None,
    generator: str = GENERATOR,
) -> FeedSnapshot:
    if items is None:
        items = [
            _entry_snap(),
            _entry_snap(
                sid="https://paulgraham.com/b.html",
                title="B",
                summary="Short summary for essay B.",
                observed=T1,
            ),
        ]
    return FeedSnapshot(
        logical_updated_at=logical_updated_at,
        generator=generator,
        index_hash=index_hash,
        index_fingerprint=index_fingerprint,
        items=items,
    )


def _catalog_entry(
    *,
    sid: str,
    title: str,
    position: int,
    observed_updated_at: datetime | None = T1,
    first_seen_at: datetime | None = T0,
    summary: str | None = None,
    published_at: datetime | None = None,
    url: str | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        stable_id=sid,
        url=url or sid,
        title=title,
        position=position,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        observed_updated_at=observed_updated_at,
        summary=summary,
        published_at=published_at,
    )


def _catalog(
    entries: list[CatalogEntry],
    *,
    last_checked_at: datetime | None = None,
) -> Catalog:
    order = [e.stable_id for e in entries]
    return Catalog(
        schema_version=1,
        material_config_fingerprint="test",
        entry_order=order,
        entries={e.stable_id: e for e in entries},
        index=ResourceState(last_checked_at=last_checked_at),
    )


def _atom_entry_updateds(raw: str) -> list[str]:
    root = ET.fromstring(raw[raw.index("<feed") :])
    return [el.text or "" for el in root.findall(f"{{{ATOM_NS}}}entry/{{{ATOM_NS}}}updated")]


def test_rss_shape_no_full_content() -> None:
    snap = _snapshot()
    raw = render_rss(snap).decode()
    assert raw.startswith("<?xml")
    assert raw.count("<item>") == len(snap.items)
    assert "content:encoded" not in raw
    assert "<rss" in raw
    root = ET.fromstring(raw[raw.index("<rss") :])
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"


def test_atom_shape_summary_only() -> None:
    snap = _snapshot()
    raw = render_atom(snap).decode()
    assert "<feed" in raw
    assert raw.count("<entry>") == len(snap.items)
    assert f'xmlns="{ATOM_NS}"' in raw
    assert "<content" not in raw
    assert "<summary" in raw


def test_atom_entry_updated_uses_observed_not_logical() -> None:
    undated = _entry_snap(
        sid="https://paulgraham.com/undated.html",
        title="Undated",
        summary="Short undated summary.",
        observed=_OBSERVED,
    )
    snap1 = _snapshot([undated], logical_updated_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC))
    snap2 = _snapshot([undated], logical_updated_at=datetime(2026, 7, 17, 18, 30, 0, tzinfo=UTC))
    raw1 = render_atom(snap1).decode()
    raw2 = render_atom(snap2).decode()
    expected = rfc3339(_OBSERVED)
    assert "1970" not in expected
    assert _atom_entry_updateds(raw1) == _atom_entry_updateds(raw2)
    assert _atom_entry_updateds(raw1) == [expected]
    feed_updated = re.findall(r"<feed[^>]*>.*?<updated>([^<]+)</updated>", raw1, re.S)
    feed_updated2 = re.findall(r"<feed[^>]*>.*?<updated>([^<]+)</updated>", raw2, re.S)
    assert feed_updated[0] == rfc3339(snap1.logical_updated_at)
    assert feed_updated2[0] == rfc3339(snap2.logical_updated_at)
    assert "<published>" not in raw1
    assert "<published>" not in raw2


def test_undated_omits_publish_dates_keeps_json_content_text() -> None:
    """Undated entries omit pub dates; JSON keeps short content_text."""
    undated = _entry_snap(
        sid="https://paulgraham.com/undated.html",
        title="Undated",
        summary="Metadata-only summary for undated essay.",
        observed=_OBSERVED,
    )
    snap = _snapshot([undated])
    rss = render_rss(snap).decode()
    atom = render_atom(snap).decode()
    item = json.loads(render_json(snap))["items"][0]

    assert "<pubDate>" not in rss
    assert "<published>" not in atom
    assert _atom_entry_updateds(atom) == [rfc3339(_OBSERVED)]
    assert "1970-01-01T00:00:00Z" not in atom
    assert "date_published" not in item
    assert item["date_modified"] == rfc3339(_OBSERVED)
    assert "content_text" in item
    assert item["content_text"] == item["summary"] == undated.summary


def test_json_feed_shape_short_content_text() -> None:
    snap = _snapshot()
    data = json.loads(render_json(snap))
    assert data["version"] == "https://jsonfeed.org/version/1.1"
    assert len(data["items"]) == len(snap.items)
    assert data["items"][0]["id"] == snap.items[0].id
    assert data["items"][0]["url"] == snap.items[0].url
    item0 = data["items"][0]
    assert "content_text" in item0
    assert item0["summary"] == snap.items[0].summary
    assert item0["content_text"] == item0["summary"]
    assert "authors" in data
    assert "authors" not in item0


def test_cross_format_id_parity() -> None:
    snap = _snapshot()
    rss = render_rss(snap).decode()
    atom = render_atom(snap).decode()
    data = json.loads(render_json(snap))
    assert snap.items[0].id in rss
    assert snap.items[0].id in atom
    assert data["items"][0]["id"] == snap.items[0].id


def test_permalink_guid_equals_url() -> None:
    snap = _snapshot([_entry_snap()])
    rss = render_rss(snap).decode()
    assert 'isPermaLink="true"' in rss
    assert '<guid isPermaLink="true">https://paulgraham.com/a.html</guid>' in rss


def test_render_uses_snapshot_summary() -> None:
    e = _entry_snap(
        sid="https://paulgraham.com/t.html",
        title="T",
        summary="Real scraped summary about startups.",
        published_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    snap = _snapshot([e])
    rss = render_rss(snap).decode()
    atom = render_atom(snap).decode()
    data = json.loads(render_json(snap))
    assert "Real scraped summary" in rss
    assert "Full body text" not in rss
    assert "content:encoded" not in rss
    assert "Real scraped summary" in atom
    assert "<content" not in atom
    item = data["items"][0]
    assert item["summary"] == "Real scraped summary about startups."
    assert item["content_text"] == item["summary"]
    assert "date_published" in item


def _write_sample(repo_root: Path, snap: FeedSnapshot | None = None) -> FeedSnapshot:
    snap = snap if snap is not None else _snapshot()
    write_feeds(
        repo_root,
        rss=render_rss(snap),
        atom=render_atom(snap),
        json_feed=render_json(snap),
        simple_rss=render_rss(snap),
        simple_atom=render_atom(snap),
        simple_json_feed=render_json(snap),
    )
    return snap


def _assert_no_staging_temps(feeds_dir: Path) -> None:
    names = (
        "rss.xml",
        "atom.xml",
        "feed.json",
        "rss.simple.xml",
        "atom.simple.xml",
        "feed.simple.json",
    )
    leftovers = [
        p
        for p in feeds_dir.iterdir()
        if p.is_file() and any(p.name.startswith(f".{name}.") for name in names)
    ]
    assert leftovers == [], f"leftover staging temps: {[p.name for p in leftovers]}"


def test_write_feeds_creates_expected_paths(repo_root: Path) -> None:
    _write_sample(repo_root)
    paths = feed_paths(repo_root)
    assert paths["rss"].is_file()
    assert paths["atom"].is_file()
    assert paths["json"].is_file()
    assert not (repo_root / "data" / "essays.json").exists()
    assert not (repo_root / "feeds" / ".manifest.json").exists()


def test_write_feeds_happy_path_and_verify(repo_root: Path) -> None:
    snap = _write_sample(repo_root)
    paths = feed_paths(repo_root)
    assert paths["rss"].is_file()
    assert paths["atom"].is_file()
    assert paths["json"].is_file()
    assert not (repo_root / "feeds" / ".manifest.json").exists()

    payload = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert len(payload["items"]) == len(snap.items)
    assert payload["items"][0]["content_text"] == payload["items"][0]["summary"]

    verify_feed_artifacts(repo_root, min_items=2)
    _assert_no_staging_temps(repo_root / "feeds")


def test_write_feeds_overwrites(repo_root: Path) -> None:
    full = _snapshot()
    one = _snapshot([full.items[0]])
    write_feeds(
        repo_root,
        rss=render_rss(full),
        atom=b"<feed/>",
        json_feed=b"{}",
        simple_rss=render_rss(full),
        simple_atom=b"<feed/>",
        simple_json_feed=b"{}",
    )
    write_feeds(
        repo_root,
        rss=render_rss(one),
        atom=b"<feed/>",
        json_feed=b"{}",
        simple_rss=render_rss(one),
        simple_atom=b"<feed/>",
        simple_json_feed=b"{}",
    )
    rss = (repo_root / "feeds" / "rss.xml").read_bytes()
    assert rss.count(b"<item>") == 1
    assert b"<title>A</title>" in rss
    assert b"<title>B</title>" not in rss


def test_feed_paths_keys(repo_root: Path) -> None:
    assert set(feed_paths(repo_root)) == {"rss", "atom", "json"}


@pytest.mark.parametrize("fail_after", [0, 1, 2])
def test_write_feeds_replace_failure_leaves_safe_state(
    repo_root: Path,
    fail_after: int,
) -> None:
    """If os.replace fails mid-publish, temps are cleaned and finals stay whole files."""
    _write_sample(repo_root)
    feeds_dir = repo_root / "feeds"
    feed_names = (
        "rss.xml",
        "atom.xml",
        "feed.json",
        "rss.simple.xml",
        "atom.simple.xml",
        "feed.simple.json",
    )
    prior = {name: (feeds_dir / name).read_bytes() for name in feed_names}

    one = _snapshot([_entry_snap()])
    new_rss = render_rss(one)
    new_atom = render_atom(one)
    new_json = render_json(one)
    new_blobs = {
        "rss.xml": new_rss,
        "atom.xml": new_atom,
        "feed.json": new_json,
        "rss.simple.xml": new_rss,
        "atom.simple.xml": new_atom,
        "feed.simple.json": new_json,
    }

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        if calls["n"] >= fail_after:
            raise OSError("simulated replace failure")
        calls["n"] += 1
        real_replace(src, dst)

    with (
        patch("paul_graham_essay_feeds.catalog.os.replace", side_effect=flaky_replace),
        pytest.raises(OSError, match="simulated replace failure"),
    ):
        write_feeds(
            repo_root,
            rss=new_rss,
            atom=new_atom,
            json_feed=new_json,
            simple_rss=new_rss,
            simple_atom=new_atom,
            simple_json_feed=new_json,
        )

    _assert_no_staging_temps(feeds_dir)

    for i, name in enumerate(feed_names):
        data = (feeds_dir / name).read_bytes()
        if i < fail_after:
            assert data == new_blobs[name]
        else:
            assert data == prior[name]
        assert data


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


def test_render_json_includes_snapshot_meta() -> None:
    snap = _snapshot(index_hash="abc123", index_fingerprint="fp-line")
    data = json.loads(render_json(snap))
    meta = data["_pg_essay_feeds"]
    assert meta["index_hash"] == "abc123"
    assert meta["index_fingerprint"] == "fp-line"
    assert meta["item_count"] == 2
    assert meta["logical_updated_at"] == rfc3339(snap.logical_updated_at)
    assert meta["generator"] == GENERATOR


# --- catalog → FeedSnapshot projection (folded from test_snapshot) ---


def test_entry_order_projection() -> None:
    """Catalog entry_order is projected in full (index-mirror membership)."""
    a = _catalog_entry(sid="https://paulgraham.com/a.html", title="A", position=0)
    b = _catalog_entry(
        sid="https://paulgraham.com/b.html",
        title="B",
        position=1,
        observed_updated_at=T2,
    )
    cat = _catalog([a, b])
    snap = catalog_to_feed_snapshot(cat, generator=GENERATOR)

    assert [i.id for i in snap.items] == [
        "https://paulgraham.com/a.html",
        "https://paulgraham.com/b.html",
    ]
    assert snap.generator == GENERATOR


def test_summary_from_catalog_and_blurb_fallback() -> None:
    with_summary = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="Alpha",
        position=0,
        summary="Source-derived short summary for Alpha.",
    )
    without = _catalog_entry(
        sid="https://paulgraham.com/b.html",
        title="Beta",
        position=1,
        summary=None,
    )
    snap = catalog_to_feed_snapshot(
        _catalog([with_summary, without]),
        generator=GENERATOR,
    )
    assert snap.items[0].summary == "Source-derived short summary for Alpha."
    assert snap.items[1].summary == blurb("Beta")


def test_summary_mode_title_only_ignores_catalog_summary() -> None:
    with_summary = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="Alpha",
        position=0,
        summary="Source-derived short summary for Alpha.",
    )
    snap = catalog_to_feed_snapshot(
        _catalog([with_summary]),
        generator=GENERATOR,
        summary_mode="title_only",
    )
    assert snap.items[0].summary == blurb("Alpha")
    assert "Source-derived" not in snap.items[0].summary


def test_write_feeds_relative_dir_custom(repo_root: Path) -> None:
    snap = _snapshot()
    write_feeds(
        repo_root,
        rss=render_rss(snap),
        atom=render_atom(snap),
        json_feed=render_json(snap),
        simple_rss=render_rss(snap),
        simple_atom=render_atom(snap),
        simple_json_feed=render_json(snap),
        relative_dir="feeds/custom",
    )
    paths = feed_paths(repo_root, relative_dir="feeds/custom")
    assert paths["rss"].is_file()
    assert paths["atom"].is_file()
    assert paths["json"].is_file()
    assert paths["rss"] == repo_root / "feeds" / "custom" / "rss.xml"


def test_verify_feed_artifacts_checks_enriched_only(repo_root: Path) -> None:
    _write_sample(repo_root)
    verify_feed_artifacts(repo_root, min_items=2)

    # Corrupt enriched tree → verify_feed_artifacts must fail.
    bad = json.loads((repo_root / "feeds" / "feed.json").read_text())
    bad["items"] = []
    (repo_root / "feeds" / "feed.json").write_text(
        json.dumps(bad) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(FeedError):
        verify_feed_artifacts(repo_root, min_items=2)


def test_observed_falls_back_to_first_seen() -> None:
    entry = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="A",
        position=0,
        observed_updated_at=None,
        first_seen_at=T0,
    )
    snap = catalog_to_feed_snapshot(_catalog([entry]), generator=GENERATOR)
    assert snap.items[0].observed_updated_at == T0
    assert snap.logical_updated_at == T0


def test_skips_entries_missing_both_timestamps() -> None:
    good = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="A",
        position=0,
        observed_updated_at=T1,
        first_seen_at=T0,
    )
    undated = _catalog_entry(
        sid="https://paulgraham.com/u.html",
        title="U",
        position=1,
        observed_updated_at=None,
        first_seen_at=None,
    )
    # H-17: undated entries fail closed (no silent omit).
    with pytest.raises(FeedError, match="lacks observed_updated_at"):
        catalog_to_feed_snapshot(_catalog([good, undated]), generator=GENERATOR)


def test_empty_catalog_raises() -> None:
    with pytest.raises(FeedError, match="no entries with observation"):
        catalog_to_feed_snapshot(_catalog([]), generator=GENERATOR)


def test_all_undated_entries_raise() -> None:
    undated = _catalog_entry(
        sid="https://paulgraham.com/u.html",
        title="U",
        position=0,
        observed_updated_at=None,
        first_seen_at=None,
    )
    with pytest.raises(FeedError, match=r"lacks observed_updated_at|no entries with observation"):
        catalog_to_feed_snapshot(_catalog([undated]), generator=GENERATOR)


def test_logical_updated_at_is_max_of_items() -> None:
    older = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="A",
        position=0,
        observed_updated_at=T0,
    )
    newer = _catalog_entry(
        sid="https://paulgraham.com/b.html",
        title="B",
        position=1,
        observed_updated_at=T2,
    )
    snap = catalog_to_feed_snapshot(_catalog([older, newer]), generator=GENERATOR)
    assert snap.logical_updated_at == T2


def test_feed_url_from_public_base() -> None:
    entry = _catalog_entry(sid="https://paulgraham.com/a.html", title="A", position=0)
    snap = catalog_to_feed_snapshot(
        _catalog([entry]),
        generator=GENERATOR,
        public_base_url="https://example.com/pg-feeds/",
    )
    assert snap.public_base_url == "https://example.com/pg-feeds/"
    assert snap.feed_url == "https://example.com/pg-feeds/feed.json"

    snap_none = catalog_to_feed_snapshot(_catalog([entry]), generator=GENERATOR)
    assert snap_none.feed_url is None
    assert snap_none.public_base_url is None


def test_rss_atom_link_self_from_public_base() -> None:
    entry = _catalog_entry(sid="https://paulgraham.com/a.html", title="A", position=0)
    snap = catalog_to_feed_snapshot(
        _catalog([entry]),
        generator=GENERATOR,
        public_base_url="https://example.com/pg-feeds/",
    )
    rss = render_rss(snap)
    root = ET.fromstring(rss)
    self_links = [el for el in root.iter(f"{{{ATOM_NS}}}link") if el.get("rel") == "self"]
    assert len(self_links) == 1
    assert self_links[0].get("type") == "application/rss+xml"
    assert self_links[0].get("href") == "https://example.com/pg-feeds/rss.xml"
    assert b"xmlns:atom=" in rss


def test_rss_no_atom_link_when_feed_url_none() -> None:
    entry = _catalog_entry(sid="https://paulgraham.com/a.html", title="A", position=0)
    snap = catalog_to_feed_snapshot(_catalog([entry]), generator=GENERATOR)
    assert snap.feed_url is None
    root = ET.fromstring(render_rss(snap))
    assert list(root.iter(f"{{{ATOM_NS}}}link")) == []


def test_rss_atom_link_self_simple_feed_url() -> None:
    snap = FeedSnapshot(
        logical_updated_at=_LOGICAL,
        generator=GENERATOR,
        feed_url="https://example.com/pg-feeds/feed.simple.json",
        items=[_entry_snap()],
    )
    root = ET.fromstring(render_rss(snap))
    self_links = [el for el in root.iter(f"{{{ATOM_NS}}}link") if el.get("rel") == "self"]
    assert len(self_links) == 1
    assert self_links[0].get("href") == "https://example.com/pg-feeds/rss.simple.xml"
    assert self_links[0].get("type") == "application/rss+xml"


def test_missing_entry_in_order_raises() -> None:
    entry = _catalog_entry(sid="https://paulgraham.com/a.html", title="A", position=0)
    with pytest.raises(ValidationError, match="entry_order"):
        Catalog(
            schema_version=1,
            material_config_fingerprint="test",
            entry_order=["https://paulgraham.com/a.html", "https://paulgraham.com/ghost.html"],
            entries={entry.stable_id: entry},
        )


def test_never_uses_1970_sentinel_in_projection() -> None:
    entry = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="A",
        position=0,
        observed_updated_at=T1,
        first_seen_at=T0,
    )
    snap = catalog_to_feed_snapshot(_catalog([entry]), generator=GENERATOR)
    assert snap.logical_updated_at.year != 1970
    assert snap.items[0].observed_updated_at.year != 1970
    atom = render_atom(snap).decode()
    assert "1970-01-01T00:00:00Z" not in atom


def test_published_at_carried_through() -> None:
    pub = datetime(2020, 5, 1, tzinfo=UTC)
    entry = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="A",
        position=0,
        published_at=pub,
        summary="Has a real published day.",
    )
    snap = catalog_to_feed_snapshot(_catalog([entry]), generator=GENERATOR)
    assert snap.items[0].published_at == pub


def test_render_snapshot_feeds_parity() -> None:
    entries = [
        _catalog_entry(
            sid="https://paulgraham.com/a.html",
            title="Alpha",
            position=0,
            summary="Alpha summary text for feeds.",
            observed_updated_at=T2,
        ),
        _catalog_entry(
            sid="https://paulgraham.com/b.html",
            title="Beta",
            position=1,
            summary="Beta summary text for feeds.",
            observed_updated_at=T1,
        ),
    ]
    snap = catalog_to_feed_snapshot(_catalog(entries), generator=GENERATOR)
    rss, atom, json_feed = render_snapshot_feeds(snap)

    assert rss.startswith(b"<?xml")
    assert atom.startswith(b"<?xml")
    payload = json.loads(json_feed.decode())
    assert len(payload["items"]) == 2
    assert payload["items"][0]["id"] == "https://paulgraham.com/a.html"
    assert payload["items"][0]["summary"] == "Alpha summary text for feeds."
    assert payload["items"][0]["content_text"] == payload["items"][0]["summary"]

    rss_root = ET.fromstring(rss[rss.index(b"<rss") :])
    assert len(rss_root.findall(".//item")) == 2
    last_build = rss_root.findtext("./channel/lastBuildDate")
    assert last_build is not None

    atom_root = ET.fromstring(atom[atom.index(b"<feed") :])
    feed_updated = atom_root.findtext(f"{{{ATOM_NS}}}updated")
    assert feed_updated == rfc3339(snap.logical_updated_at)
    assert len(atom_root.findall(f"{{{ATOM_NS}}}entry")) == 2


def test_summary_truncated_to_feed_limit() -> None:
    long = "word " * 200
    entry = _catalog_entry(
        sid="https://paulgraham.com/a.html",
        title="Long",
        position=0,
        summary=long,
    )
    snap = catalog_to_feed_snapshot(_catalog([entry]), generator=GENERATOR)
    assert 1 <= len(snap.items[0].summary) <= FEED_SUMMARY_CHARS


def test_turbify_non_permalink_renders() -> None:
    sid = "urn:uuid:11111111-1111-5111-8111-111111111111"
    entry = _catalog_entry(
        sid=sid,
        title="Chapter 1",
        position=0,
        url="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt",
        summary="Protected chapter summary.",
    )
    snap = catalog_to_feed_snapshot(_catalog([entry]), generator=GENERATOR)
    rss, _atom, _jf = render_snapshot_feeds(snap)
    assert b'isPermaLink="false"' in rss
    assert sid.encode() in rss


def test_catalog_to_feed_snapshot_carries_index_hashes() -> None:
    entry = _catalog_entry(sid="https://paulgraham.com/a.html", title="A", position=0)
    snap = catalog_to_feed_snapshot(
        _catalog([entry]),
        generator=GENERATOR,
        index_hash="deadbeef",
        index_fingerprint="fp\nline",
    )
    assert snap.index_hash == "deadbeef"
    assert snap.index_fingerprint == "fp\nline"
    meta = json.loads(render_json(snap))["_pg_essay_feeds"]
    assert meta["index_hash"] == "deadbeef"
    assert meta["index_fingerprint"] == "fp\nline"


def test_same_snapshot_renders_deterministically() -> None:
    snap = _snapshot()
    assert render_rss(snap) == render_rss(snap)
    assert render_atom(snap) == render_atom(snap)
    assert render_json(snap) == render_json(snap)


def test_golden_fixtures_parity() -> None:
    """Render matches committed P0.3 goldens under tests/fixtures/feeds/."""
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "feeds"
    snap = FeedSnapshot(
        logical_updated_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        generator="pg-essay-feeds/0.1.0",
        index_hash="abc123",
        index_fingerprint=(
            "1\thttps://paulgraham.com/a.html\thttps://paulgraham.com/a.html\tAlpha"
        ),
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="Alpha",
                summary="Alpha summary text for feeds.",
                observed_updated_at=datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC),
            ),
            FeedEntrySnapshot(
                id="https://paulgraham.com/b.html",
                url="https://paulgraham.com/b.html",
                title="Beta",
                summary="Beta summary text for feeds.",
                observed_updated_at=datetime(2024, 6, 15, 9, 30, 0, tzinfo=UTC),
            ),
            FeedEntrySnapshot(
                id="urn:uuid:11111111-1111-5111-8111-111111111111",
                url="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt",
                title="Chapter 1",
                summary="Protected chapter summary.",
                observed_updated_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            ),
        ],
    )
    assert render_rss(snap) == (fixtures / "golden.rss.xml").read_bytes()
    assert render_atom(snap) == (fixtures / "golden.atom.xml").read_bytes()
    assert render_json(snap) == (fixtures / "golden.feed.json").read_bytes()


def test_simple_golden_fixtures_parity() -> None:
    """title_only renders match committed simple goldens under tests/fixtures/feeds/."""
    fixtures = Path(__file__).resolve().parents[1] / "fixtures" / "feeds"
    snap = FeedSnapshot(
        logical_updated_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        generator="pg-essay-feeds/0.1.0",
        index_hash="abc123",
        index_fingerprint=(
            "1\thttps://paulgraham.com/a.html\thttps://paulgraham.com/a.html\tAlpha"
        ),
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="Alpha",
                summary=blurb("Alpha"),
                observed_updated_at=datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC),
            ),
            FeedEntrySnapshot(
                id="https://paulgraham.com/b.html",
                url="https://paulgraham.com/b.html",
                title="Beta",
                summary=blurb("Beta"),
                observed_updated_at=datetime(2024, 6, 15, 9, 30, 0, tzinfo=UTC),
            ),
            FeedEntrySnapshot(
                id="urn:uuid:11111111-1111-5111-8111-111111111111",
                url="https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt",
                title="Chapter 1",
                summary=blurb("Chapter 1"),
                observed_updated_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
            ),
        ],
    )
    assert render_rss(snap) == (fixtures / "golden.rss.simple.xml").read_bytes()
    assert render_atom(snap) == (fixtures / "golden.atom.simple.xml").read_bytes()
    assert render_json(snap) == (fixtures / "golden.feed.simple.json").read_bytes()
