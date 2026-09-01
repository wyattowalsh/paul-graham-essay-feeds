"""GitHub Pages assemble: committed feeds plus /latest projection."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from paul_graham_essay_feeds.models import ATOM_NS, LATEST_FEED_ITEMS, FeedError
from paul_graham_essay_feeds.pages import (
    assemble_pages,
    index_html,
    kind_for_name,
    main,
    slice_latest,
)

_REPO = Path(__file__).resolve().parents[2]


def test_kind_for_name() -> None:
    assert kind_for_name("rss.xml") == "rss"
    assert kind_for_name("rss.simple.xml") == "rss"
    assert kind_for_name("atom.xml") == "atom"
    assert kind_for_name("atom.simple.xml") == "atom"
    assert kind_for_name("feed.json") == "json"
    assert kind_for_name("feed.simple.json") == "json"


def test_slice_latest_json_caps_and_rewrites_title() -> None:
    items = [{"id": f"https://paulgraham.com/{i}.html"} for i in range(25)]
    raw = json.dumps({"title": "Paul Graham Essays — Enriched (Unofficial)", "items": items})
    sliced = json.loads(slice_latest(raw, "json"))
    assert len(sliced["items"]) == LATEST_FEED_ITEMS
    assert sliced["items"][0]["id"] == "https://paulgraham.com/0.html"
    assert "Latest enriched" in sliced["title"]


def test_slice_latest_rss_keeps_header_and_first_n() -> None:
    items = "".join(f"<item><title>T{i}</title></item>" for i in range(5))
    xml = f"<rss><channel><title>X</title>{items}</channel></rss>"
    out = slice_latest(xml, "rss", limit=2)
    assert out.count("<item>") == 2
    assert "<title>X</title>" in out
    assert "<title>T0</title>" in out
    assert "<title>T1</title>" in out
    assert "<title>T4</title>" not in out


def test_slice_latest_short_xml_is_unchanged() -> None:
    xml = "<feed><entry><title>A</title></entry></feed>"
    assert slice_latest(xml, "atom", limit=20) == xml


def test_slice_latest_rejects_non_object_json() -> None:
    with pytest.raises(FeedError, match="object"):
        slice_latest("[]", "json")
    with pytest.raises(FeedError, match="valid JSON"):
        slice_latest("{", "json")


def test_slice_latest_json_non_list_items_still_rewrites_title() -> None:
    raw = json.dumps({"title": "Paul Graham Essays — Enriched (Unofficial)", "items": "nope"})
    data = json.loads(slice_latest(raw, "json"))
    assert data["items"] == "nope"
    assert "Latest enriched" in data["title"]


def test_assemble_pages_writes_feeds_latest_and_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    feeds = repo / "feeds"
    feeds.mkdir(parents=True)
    rss_items = "".join(f"<item><title>T{i}</title></item>" for i in range(3))
    (feeds / "rss.xml").write_text(f"<rss><channel>{rss_items}</channel></rss>", encoding="utf-8")
    (feeds / "atom.xml").write_text(
        "<feed><entry><title>A</title></entry></feed>", encoding="utf-8"
    )
    payload = {
        "title": "Paul Graham Essays — Simple (Unofficial)",
        "items": [{"id": "https://paulgraham.com/a.html"} for _ in range(2)],
    }
    (feeds / "feed.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")
    for name in ("rss.simple.xml", "atom.simple.xml", "feed.simple.json"):
        src = "rss.xml" if name.startswith("rss") else "atom.xml" if "atom" in name else "feed.json"
        (feeds / name).write_bytes((feeds / src).read_bytes())

    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    assert (dest / ".nojekyll").is_file()
    html = (dest / "index.html").read_text(encoding="utf-8")
    assert "<body>" in html
    assert "Paul Graham essay feeds" in html
    assert (dest / "rss.xml").read_bytes() == (feeds / "rss.xml").read_bytes()
    assert (dest / "feeds" / "rss.xml").read_bytes() == (feeds / "rss.xml").read_bytes()
    latest_rss = (dest / "latest" / "rss.xml").read_text(encoding="utf-8")
    assert latest_rss.count("<item>") == 3
    latest_json = json.loads((dest / "latest" / "feed.json").read_text(encoding="utf-8"))
    assert "Latest simple" in latest_json["title"]


def test_assemble_pages_missing_feeds(tmp_path: Path) -> None:
    with pytest.raises(FeedError, match="Missing feeds"):
        assemble_pages(tmp_path, tmp_path / "out")


def test_assemble_pages_refuses_repo_root(tmp_path: Path) -> None:
    (tmp_path / "feeds").mkdir()
    with pytest.raises(FeedError, match="repository root"):
        assemble_pages(tmp_path, tmp_path)


def test_assemble_pages_replaces_stale_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    feeds = repo / "feeds"
    feeds.mkdir(parents=True)
    body = "<rss><channel><item><title>A</title></item></channel></rss>"
    json_body = '{"title":"T","items":[]}\n'
    for name in ("rss.xml", "atom.xml", "rss.simple.xml", "atom.simple.xml"):
        (feeds / name).write_text(body, encoding="utf-8")
    (feeds / "feed.json").write_text(json_body, encoding="utf-8")
    (feeds / "feed.simple.json").write_text(json_body, encoding="utf-8")
    dest = tmp_path / "_site"
    dest.mkdir()
    (dest / "stale.txt").write_text("leftover", encoding="utf-8")
    assemble_pages(repo, dest)
    assert not (dest / "stale.txt").exists()
    assert (dest / "rss.xml").is_file()


def test_assemble_committed_feeds_latest_is_well_formed(tmp_path: Path) -> None:
    dest = tmp_path / "_site"
    assemble_pages(_REPO, dest)
    assert (dest / "rss.xml").read_bytes() == (_REPO / "feeds" / "rss.xml").read_bytes()
    rss = ET.parse(dest / "latest" / "rss.xml")
    assert len(list(rss.getroot().iter("item"))) == LATEST_FEED_ITEMS
    atom = ET.parse(dest / "latest" / "atom.xml")
    assert len(atom.getroot().findall(f"{{{ATOM_NS}}}entry")) == LATEST_FEED_ITEMS
    data = json.loads((dest / "latest" / "feed.json").read_text(encoding="utf-8"))
    assert len(data["items"]) == LATEST_FEED_ITEMS
    assert "Latest" in data["title"]


def test_assemble_pages_missing_artifact(tmp_path: Path) -> None:
    (tmp_path / "feeds").mkdir()
    with pytest.raises(FeedError, match="Missing feed artifact"):
        assemble_pages(tmp_path, tmp_path / "out")


def test_index_html_uses_relative_links() -> None:
    html = index_html()
    assert "href='rss.simple.xml'" in html or 'href="rss.simple.xml"' in html
    assert "latest/rss.xml" in html
    assert "<head>" in html
    assert "<body>" in html
    assert "site/" not in html


def test_assemble_pages_replaces_file_dest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    feeds = repo / "feeds"
    feeds.mkdir(parents=True)
    body = "<rss><channel><item><title>A</title></item></channel></rss>"
    json_body = '{"title":"T","items":[]}\n'
    for name in ("rss.xml", "atom.xml", "rss.simple.xml", "atom.simple.xml"):
        (feeds / name).write_text(body, encoding="utf-8")
    (feeds / "feed.json").write_text(json_body, encoding="utf-8")
    (feeds / "feed.simple.json").write_text(json_body, encoding="utf-8")
    dest = tmp_path / "built"
    dest.write_text("not a directory", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assemble_pages(repo, Path("built"))
    assert dest.is_dir()
    assert (dest / "rss.xml").is_file()


def test_pages_main_writes_out(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    feeds = repo / "feeds"
    feeds.mkdir(parents=True)
    body = "<rss><channel><item><title>A</title></item></channel></rss>"
    json_body = '{"title":"T","items":[]}\n'
    for name in (
        "rss.xml",
        "atom.xml",
        "rss.simple.xml",
        "atom.simple.xml",
    ):
        (feeds / name).write_text(body, encoding="utf-8")
    (feeds / "feed.json").write_text(json_body, encoding="utf-8")
    (feeds / "feed.simple.json").write_text(json_body, encoding="utf-8")
    dest = tmp_path / "built"
    assert main(["--repo-root", str(repo), "--out", str(dest)]) == 0
    assert (dest / "feed.json").is_file()
    assert (dest / "latest" / "atom.xml").is_file()
