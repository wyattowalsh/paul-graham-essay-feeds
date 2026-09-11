"""GitHub Pages assemble: committed feeds plus /latest projection."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from paul_graham_essay_feeds.feeds import feed_self_url
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    FEED_ID,
    FEED_ID_LATEST,
    FEED_ID_SIMPLE,
    FEED_ID_SIMPLE_LATEST,
    FEED_TITLE_LATEST,
    HOST_PUBLIC_BASE_URL,
    LATEST_FEED_ITEMS,
    FeedError,
)
from paul_graham_essay_feeds.pages import (
    assemble_pages,
    index_html,
    is_simple_feed_name,
    kind_for_name,
    latest_atom_id,
    latest_description,
    latest_json_feed_url,
    latest_title,
    main,
    slice_latest,
    verify_pages_artifact,
)

_REPO = Path(__file__).resolve().parents[2]
_HOST = HOST_PUBLIC_BASE_URL.rstrip("/")
_LATEST_NAMES = (
    "rss.xml",
    "atom.xml",
    "feed.json",
    "rss.simple.xml",
    "atom.simple.xml",
    "feed.simple.json",
)


def _rss_xml(n: int, *, simple: bool = False) -> str:
    items = "".join(
        "<item>"
        f"<title>T{i}</title>"
        f"<link>https://paulgraham.com/{i}.html</link>"
        f"<guid isPermaLink='true'>https://paulgraham.com/{i}.html</guid>"
        f"<description>D{i}</description>"
        "</item>"
        for i in range(n)
    )
    variant = "Simple" if simple else "Enriched"
    name = "rss.simple.xml" if simple else "rss.xml"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">'
        "<channel>"
        f"<title>Paul Graham Essays — {variant} (Unofficial)</title>"
        "<description>full description</description>"
        f'<atom:link rel="self" type="application/rss+xml" href="{_HOST}/{name}" />'
        f"{items}"
        "</channel></rss>\n"
    )


def _atom_xml(n: int, *, simple: bool = False) -> str:
    entries = "".join(
        "<entry>"
        f"<title>T{i}</title>"
        f"<id>https://paulgraham.com/{i}.html</id>"
        f"<updated>2026-01-0{1 + (i % 9)}T00:00:00Z</updated>"
        f'<summary type="text">D{i}</summary>'
        f'<link rel="alternate" href="https://paulgraham.com/{i}.html" />'
        "</entry>"
        for i in range(n)
    )
    variant = "Simple" if simple else "Enriched"
    feed_id = FEED_ID_SIMPLE if simple else FEED_ID
    name = "atom.simple.xml" if simple else "atom.xml"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<feed xmlns="{ATOM_NS}" xml:lang="en">'
        f"<title>Paul Graham Essays — {variant} (Unofficial)</title>"
        f"<id>{feed_id}</id>"
        "<subtitle>full description</subtitle>"
        f'<link rel="self" type="application/atom+xml" href="{_HOST}/{name}" />'
        f"{entries}"
        "</feed>\n"
    )


def _json_feed(n: int, *, simple: bool = False) -> str:
    variant = "Simple" if simple else "Enriched"
    name = "feed.simple.json" if simple else "feed.json"
    items = [
        {
            "id": f"https://paulgraham.com/{i}.html",
            "url": f"https://paulgraham.com/{i}.html",
            "title": f"T{i}",
            "summary": f"D{i}",
            "content_text": f"D{i}",
            "date_modified": f"2026-01-0{1 + (i % 9)}T00:00:00Z",
        }
        for i in range(n)
    ]
    payload = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": f"Paul Graham Essays — {variant} (Unofficial)",
        "description": "full description",
        "feed_url": f"{_HOST}/{name}",
        "items": items,
        "_pg_essay_feeds": {"item_count": n, "generator": "test"},
    }
    return json.dumps(payload, indent=2) + "\n"


def _populate_feeds(feeds: Path, n: int) -> None:
    feeds.mkdir(parents=True, exist_ok=True)
    (feeds / "rss.xml").write_text(_rss_xml(n), encoding="utf-8")
    (feeds / "rss.simple.xml").write_text(_rss_xml(n, simple=True), encoding="utf-8")
    (feeds / "atom.xml").write_text(_atom_xml(n), encoding="utf-8")
    (feeds / "atom.simple.xml").write_text(_atom_xml(n, simple=True), encoding="utf-8")
    (feeds / "feed.json").write_text(_json_feed(n), encoding="utf-8")
    (feeds / "feed.simple.json").write_text(_json_feed(n, simple=True), encoding="utf-8")


def _rss_self(path: Path) -> str | None:
    root = ET.parse(path).getroot()
    for el in root.iter():
        if el.tag.endswith("link") and el.get("rel") == "self":
            return el.get("href")
    return None


def _atom_self(path: Path) -> str | None:
    root = ET.parse(path).getroot()
    for el in root.findall(f"{{{ATOM_NS}}}link"):
        if el.get("rel") == "self":
            return el.get("href")
    return None


def _atom_id(path: Path) -> str | None:
    return ET.parse(path).getroot().findtext(f"{{{ATOM_NS}}}id")


def test_kind_for_name() -> None:
    assert kind_for_name("rss.xml") == "rss"
    assert kind_for_name("rss.simple.xml") == "rss"
    assert kind_for_name("atom.xml") == "atom"
    assert kind_for_name("atom.simple.xml") == "atom"
    assert kind_for_name("feed.json") == "json"
    assert kind_for_name("feed.simple.json") == "json"
    assert is_simple_feed_name("rss.simple.xml")
    assert not is_simple_feed_name("rss.xml")


def test_latest_identity_constants_are_distinct() -> None:
    assert f"{FEED_ID}:latest" == FEED_ID_LATEST
    assert f"{FEED_ID_SIMPLE}:latest" == FEED_ID_SIMPLE_LATEST
    assert len({FEED_ID, FEED_ID_SIMPLE, FEED_ID_LATEST, FEED_ID_SIMPLE_LATEST}) == 4
    assert latest_atom_id(simple=False) == FEED_ID_LATEST
    assert latest_atom_id(simple=True) == FEED_ID_SIMPLE_LATEST
    assert "Latest 20" in latest_title(simple=False)
    assert "Enriched" in latest_title(simple=False)
    assert "Simple" in latest_title(simple=True)
    assert "Latest 2" in latest_title(simple=False, limit=2)
    assert "bounded newest-first" in latest_description().lower()


def test_slice_latest_json_caps_and_rewrites_identity() -> None:
    items = [
        {
            "id": f"https://paulgraham.com/{i}.html",
            "url": f"https://paulgraham.com/{i}.html",
            "title": f"T{i}",
            "summary": f"D{i}",
            "content_text": f"D{i}",
        }
        for i in range(25)
    ]
    raw = json.dumps(
        {
            "title": "Paul Graham Essays — Enriched (Unofficial)",
            "feed_url": f"{_HOST}/feed.json",
            "items": items,
            "_pg_essay_feeds": {"item_count": 25},
        }
    )
    sliced = json.loads(slice_latest(raw, "json"))
    assert len(sliced["items"]) == LATEST_FEED_ITEMS
    assert sliced["items"][0]["id"] == "https://paulgraham.com/0.html"
    assert sliced["title"] == FEED_TITLE_LATEST
    assert sliced["feed_url"] == f"{_HOST}/latest/feed.json"
    assert "bounded newest-first" in sliced["description"].lower()
    assert sliced["_pg_essay_feeds"]["item_count"] == LATEST_FEED_ITEMS


def test_slice_latest_rss_rewrites_header_and_keeps_first_n() -> None:
    out = slice_latest(_rss_xml(5), "rss", limit=2)
    root = ET.fromstring(out)
    items = [el for el in root.iter("item")]
    assert len(items) == 2
    title = next(el.text for el in root.iter("title") if el.text and "Latest" in el.text)
    assert "Latest 2" in title
    assert "Enriched" in title
    assert items[0].findtext("title") == "T0"
    assert items[1].findtext("title") == "T1"
    assert not any(el.findtext("title") == "T4" for el in items)
    self_href = next(
        el.get("href") for el in root.iter() if el.tag.endswith("link") and el.get("rel") == "self"
    )
    assert self_href == f"{_HOST}/latest/rss.xml"


def test_slice_latest_short_xml_still_rewrites_identity() -> None:
    out = slice_latest(_atom_xml(1), "atom", limit=20)
    root = ET.fromstring(out)
    assert root.findtext(f"{{{ATOM_NS}}}id") == FEED_ID_LATEST
    assert root.findtext(f"{{{ATOM_NS}}}title") == FEED_TITLE_LATEST
    self_href = next(
        el.get("href") for el in root.findall(f"{{{ATOM_NS}}}link") if el.get("rel") == "self"
    )
    assert self_href == f"{_HOST}/latest/atom.xml"
    assert len(root.findall(f"{{{ATOM_NS}}}entry")) == 1


def test_slice_latest_zero_items_rewrites_metadata() -> None:
    out = slice_latest(_rss_xml(0), "rss", limit=20)
    root = ET.fromstring(out)
    assert list(root.iter("item")) == []
    title = next(el.text for el in root.iter("title") if el.text)
    assert title == FEED_TITLE_LATEST
    json_out = json.loads(slice_latest(_json_feed(0), "json"))
    assert json_out["items"] == []
    assert json_out["title"] == FEED_TITLE_LATEST
    assert json_out["feed_url"] == f"{_HOST}/latest/feed.json"


def test_slice_latest_rejects_non_object_json() -> None:
    with pytest.raises(FeedError, match="object"):
        slice_latest("[]", "json")
    with pytest.raises(FeedError, match="valid JSON"):
        slice_latest("{", "json")


def test_slice_latest_json_non_list_items_fails_closed() -> None:
    raw = json.dumps({"title": "Paul Graham Essays — Enriched (Unofficial)", "items": "nope"})
    with pytest.raises(FeedError, match="items must be a list"):
        slice_latest(raw, "json")


def test_slice_latest_malformed_rss_and_atom_fail_closed() -> None:
    with pytest.raises(FeedError, match="well-formed"):
        slice_latest("<rss><channel>", "rss")
    with pytest.raises(FeedError, match="namespaced"):
        slice_latest("<feed><entry><title>A</title></entry></feed>", "atom")
    with pytest.raises(FeedError, match="well-formed"):
        slice_latest("<feed", "atom")


def test_assemble_pages_writes_feeds_latest_and_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 3)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    verify_pages_artifact(dest, repo_root=repo)
    assert (dest / ".nojekyll").is_file()
    html = (dest / "index.html").read_text(encoding="utf-8")
    assert "Paul Graham essay feeds" in html
    assert "latest/rss.simple.xml" in html
    assert (dest / "rss.xml").read_bytes() == (repo / "feeds" / "rss.xml").read_bytes()
    assert (dest / "feeds" / "rss.xml").read_bytes() == (repo / "feeds" / "rss.xml").read_bytes()
    latest_rss = ET.parse(dest / "latest" / "rss.xml")
    assert len(list(latest_rss.getroot().iter("item"))) == 3
    latest_json = json.loads((dest / "latest" / "feed.json").read_text(encoding="utf-8"))
    assert latest_json["title"] == FEED_TITLE_LATEST


def test_assemble_pages_missing_feeds(tmp_path: Path) -> None:
    with pytest.raises(FeedError, match="Missing feeds"):
        assemble_pages(tmp_path, tmp_path / "out")


def test_assemble_pages_refuses_repo_root(tmp_path: Path) -> None:
    (tmp_path / "feeds").mkdir()
    with pytest.raises(FeedError, match="repository root"):
        assemble_pages(tmp_path, tmp_path)


def test_assemble_pages_replaces_stale_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 1)
    dest = tmp_path / "_site"
    dest.mkdir()
    (dest / "stale.txt").write_text("leftover", encoding="utf-8")
    assemble_pages(repo, dest)
    assert not (dest / "stale.txt").exists()
    assert (dest / "rss.xml").is_file()
    verify_pages_artifact(dest, repo_root=repo)


def test_verify_pages_artifact_rejects_stale_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 1)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    (dest / "stale.txt").write_text("leftover", encoding="utf-8")
    with pytest.raises(FeedError, match="file set mismatch"):
        verify_pages_artifact(dest, repo_root=repo)


def test_assemble_committed_feeds_latest_identity(tmp_path: Path) -> None:
    dest = tmp_path / "_site"
    assemble_pages(_REPO, dest)
    verify_pages_artifact(dest, repo_root=_REPO)
    assert (dest / "rss.xml").read_bytes() == (_REPO / "feeds" / "rss.xml").read_bytes()
    assert (dest / "feeds" / "rss.xml").read_bytes() == (_REPO / "feeds" / "rss.xml").read_bytes()
    ids: list[str] = []
    for name in _LATEST_NAMES:
        latest = dest / "latest" / name
        assert latest.is_file()
        simple = ".simple." in name
        if name.endswith(".json"):
            data = json.loads(latest.read_text(encoding="utf-8"))
            full = json.loads((dest / name).read_text(encoding="utf-8"))
            assert data["feed_url"] == latest_json_feed_url(simple=simple)
            assert data["title"] == latest_title(simple=simple)
            assert len(data["items"]) == LATEST_FEED_ITEMS
            assert data["items"] == full["items"][:LATEST_FEED_ITEMS]
        elif "atom" in name:
            root = ET.parse(latest).getroot()
            full_root = ET.parse(dest / name).getroot()
            assert _atom_id(latest) == latest_atom_id(simple=simple)
            assert _atom_self(latest) == feed_self_url(
                latest_json_feed_url(simple=simple), kind="atom"
            )
            assert root.findtext(f"{{{ATOM_NS}}}title") == latest_title(simple=simple)
            latest_entries = root.findall(f"{{{ATOM_NS}}}entry")
            full_entries = full_root.findall(f"{{{ATOM_NS}}}entry")
            assert len(latest_entries) == LATEST_FEED_ITEMS
            assert [el.findtext(f"{{{ATOM_NS}}}id") for el in latest_entries] == [
                el.findtext(f"{{{ATOM_NS}}}id") for el in full_entries[:LATEST_FEED_ITEMS]
            ]
            ids.append(_atom_id(latest) or "")
        else:
            root = ET.parse(latest).getroot()
            full_root = ET.parse(dest / name).getroot()
            assert _rss_self(latest) == feed_self_url(
                latest_json_feed_url(simple=simple), kind="rss"
            )
            channel = next(el for el in root if el.tag.endswith("channel") or el.tag == "channel")
            title = next(el.text for el in channel if el.tag == "title")
            assert title == latest_title(simple=simple)
            latest_items = list(root.iter("item"))
            full_items = list(full_root.iter("item"))
            assert len(latest_items) == LATEST_FEED_ITEMS
            assert [el.findtext("guid") for el in latest_items] == [
                el.findtext("guid") for el in full_items[:LATEST_FEED_ITEMS]
            ]
    assert ids == [FEED_ID_LATEST, FEED_ID_SIMPLE_LATEST]


def test_assemble_pages_is_deterministic(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    assemble_pages(_REPO, a)
    assemble_pages(_REPO, b)
    names_a = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    names_b = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    assert names_a == names_b
    for rel in names_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_verify_rejects_full_feed_self_url_on_latest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 3)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    dest.joinpath("latest", "rss.xml").write_bytes((dest / "rss.xml").read_bytes())
    with pytest.raises(FeedError, match="self URL"):
        verify_pages_artifact(dest, repo_root=repo)


def test_verify_rejects_reordered_latest_items(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 3)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    data = json.loads((dest / "latest" / "feed.json").read_text(encoding="utf-8"))
    data["items"] = list(reversed(data["items"]))
    (dest / "latest" / "feed.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(FeedError, match="ordered prefix"):
        verify_pages_artifact(dest, repo_root=repo)


def test_verify_rejects_changed_latest_item_url(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 3)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    data = json.loads((dest / "latest" / "feed.json").read_text(encoding="utf-8"))
    data["items"][0]["url"] = "https://example.com/changed"
    (dest / "latest" / "feed.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(FeedError, match="ordered prefix"):
        verify_pages_artifact(dest, repo_root=repo)


def test_verify_rejects_duplicate_latest_ids(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 3)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    data = json.loads((dest / "latest" / "feed.json").read_text(encoding="utf-8"))
    data["items"][1]["id"] = data["items"][0]["id"]
    (dest / "latest" / "feed.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(FeedError, match="duplicate item ids"):
        verify_pages_artifact(dest, repo_root=repo)


def test_verify_rejects_incorrect_latest_title(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 3)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    data = json.loads((dest / "latest" / "feed.json").read_text(encoding="utf-8"))
    data["title"] = "Paul Graham Essays — Enriched (Unofficial)"
    (dest / "latest" / "feed.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(FeedError, match="title"):
        verify_pages_artifact(dest, repo_root=repo)


def test_verify_rejects_atom_id_collision_with_full_feed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 2)
    dest = tmp_path / "_site"
    assemble_pages(repo, dest)
    text = (dest / "latest" / "atom.xml").read_text(encoding="utf-8")
    text = text.replace(FEED_ID_LATEST, FEED_ID, 1)
    (dest / "latest" / "atom.xml").write_text(text, encoding="utf-8")
    with pytest.raises(FeedError, match="Atom id"):
        verify_pages_artifact(dest, repo_root=repo)


def test_assemble_pages_missing_artifact(tmp_path: Path) -> None:
    (tmp_path / "feeds").mkdir()
    with pytest.raises(FeedError, match="Missing feed artifact"):
        assemble_pages(tmp_path, tmp_path / "out")


def test_index_html_uses_relative_links() -> None:
    html = index_html()
    assert "href='rss.simple.xml'" in html or 'href="rss.simple.xml"' in html
    assert "latest/rss.xml" in html
    assert "latest/rss.simple.xml" in html
    assert "latest/atom.simple.xml" in html
    assert "latest/feed.simple.json" in html
    assert "<head>" in html
    assert "<body>" in html
    assert "site/" not in html


def test_assemble_pages_replaces_file_dest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 1)
    dest = tmp_path / "built"
    dest.write_text("not a directory", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assemble_pages(repo, Path("built"))
    assert dest.is_dir()
    assert (dest / "rss.xml").is_file()


def test_pages_main_writes_and_verifies(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _populate_feeds(repo / "feeds", 1)
    dest = tmp_path / "built"
    assert main(["--repo-root", str(repo), "--out", str(dest)]) == 0
    assert (dest / "feed.json").is_file()
    assert (dest / "latest" / "atom.xml").is_file()
    verify_pages_artifact(dest, repo_root=repo)


def test_pages_module_does_not_use_regex_item_slicing() -> None:
    source = Path(__file__).resolve().parents[2] / "src" / "paul_graham_essay_feeds" / "pages.py"
    text = source.read_text(encoding="utf-8")
    assert "_ITEM_RE" not in text
    assert 'findall(r"<item' not in text
    assert 'findall(r"<entry' not in text
