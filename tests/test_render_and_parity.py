"""Renderer structure and cross-format parity tests."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest

from paul_graham_essay_feeds.domain import (
    BuildContext,
    EssayItem,
    FeedError,
    PublicUrls,
    make_stable_id,
)
from paul_graham_essay_feeds.renderers import (
    render_atom,
    render_json_feed,
    render_opml,
    render_rss,
)
from paul_graham_essay_feeds.validation import (
    assert_cross_format_parity,
    validate_atom_bytes,
    validate_json_feed_bytes,
    validate_opml_bytes,
    validate_rss_bytes,
)

NOW = datetime(2026, 7, 11, 7, 24, 19, tzinfo=UTC)


def _items() -> tuple[EssayItem, ...]:
    out = []
    for i, slug in enumerate(("a", "b"), start=1):
        url = f"https://paulgraham.com/{slug}.html"
        sid, perm = make_stable_id(url)
        out.append(EssayItem(i, slug.upper(), url, sid, perm, NOW, NOW))
    return tuple(out)


def _ctx(public: bool = True) -> BuildContext:
    pub = PublicUrls.from_base("https://example.test/feeds-site/") if public else None
    return BuildContext(
        items=_items(),
        feed_title="Paul Graham: Essays",
        feed_description="Desc",
        author_name="Paul Graham",
        author_url="https://paulgraham.com/",
        language="en",
        home_page_url="https://paulgraham.com/articles.html",
        public=pub,
        feed_id="tag:wyattowalsh.github.io,2026:paul-graham-essay-feeds",
        generator="pg-essay-feeds/0.1.0",
        build_updated_at=NOW,
        category="Essays",
    )


def test_rss_structure_and_no_pubdate() -> None:
    ctx = _ctx()
    raw = render_rss(ctx)
    root = ET.fromstring(raw)
    assert root.tag == "rss"
    assert root.attrib["version"] == "2.0"
    channel = root.find("channel")
    assert channel is not None
    assert channel.findtext("language") == "en-US"
    for item in channel.findall("item"):
        assert item.find("pubDate") is None
        assert item.find("category") is not None
        assert item.find("guid") is not None
    validate_rss_bytes(
        raw,
        expected_items=ctx.items,
        min_items=1,
        public=ctx.public,
        generator=ctx.generator,
    )


def test_atom_required_fields() -> None:
    ctx = _ctx()
    raw = render_atom(ctx)
    validate_atom_bytes(
        raw,
        expected_items=ctx.items,
        min_items=1,
        public=ctx.public,
        feed_id=ctx.feed_id,
    )
    root = ET.fromstring(raw)
    assert any(el.tag.endswith("updated") for el in list(root))
    assert not any(el.tag.endswith("published") for el in root.iter())


def test_json_feed_version_and_content() -> None:
    ctx = _ctx()
    raw = render_json_feed(ctx)
    data = json.loads(raw)
    assert data["version"] == "https://jsonfeed.org/version/1.1"
    assert "authors" in data
    assert all("content_text" in item for item in data["items"])
    assert all("date_published" not in item for item in data["items"])
    validate_json_feed_bytes(raw, expected_items=ctx.items, min_items=1, public=ctx.public)


def test_opml_requires_public_url() -> None:
    with pytest.raises(FeedError):
        render_opml(_ctx(public=False))


def test_opml_catalog() -> None:
    ctx = _ctx()
    assert ctx.public is not None
    raw = render_opml(ctx)
    validate_opml_bytes(raw, public=ctx.public)


def test_cross_format_parity() -> None:
    ctx = _ctx()
    rss = render_rss(ctx)
    atom = render_atom(ctx)
    jf = render_json_feed(ctx)
    parity = assert_cross_format_parity(items=ctx.items, rss=rss, atom=atom, json_feed=jf)
    assert all(parity.values())
