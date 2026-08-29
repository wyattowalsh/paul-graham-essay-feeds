"""AUD-006: public_base_url rejects query/fragment/userinfo; canonical self links."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.feeds import (
    catalog_to_feed_snapshot,
    feed_self_url,
    render_atom,
    render_json,
    render_rss,
)
from paul_graham_essay_feeds.models import (
    ATOM_NS,
    Catalog,
    CatalogEntry,
    ConfigurationError,
    ResourceState,
)
from paul_graham_essay_feeds.settings import Settings

GENERATOR = "pg-essay-feeds/test"
T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_INVALID = (ValidationError, ConfigurationError)


def _catalog() -> Catalog:
    sid = "https://paulgraham.com/a.html"
    entry = CatalogEntry(
        stable_id=sid,
        url=sid,
        title="A",
        position=0,
        first_seen_at=T0,
        last_seen_at=T0,
        observed_updated_at=T0,
        summary="Short summary for essay A.",
    )
    return Catalog(
        schema_version=1,
        material_config_fingerprint="test",
        entry_order=[sid],
        entries={sid: entry},
        index=ResourceState(),
    )


def _self_hrefs(public_base_url: str) -> tuple[str, str, str]:
    snap = catalog_to_feed_snapshot(
        _catalog(),
        generator=GENERATOR,
        public_base_url=public_base_url,
    )
    assert snap.feed_url is not None
    rss_root = ET.fromstring(render_rss(snap))
    rss_hrefs = [
        el.get("href") for el in rss_root.iter(f"{{{ATOM_NS}}}link") if el.get("rel") == "self"
    ]
    atom_root = ET.fromstring(render_atom(snap))
    atom_hrefs = [
        el.get("href") for el in atom_root.iter(f"{{{ATOM_NS}}}link") if el.get("rel") == "self"
    ]
    json_url = json.loads(render_json(snap))["feed_url"]
    assert len(rss_hrefs) == 1 and rss_hrefs[0]
    assert len(atom_hrefs) == 1 and atom_hrefs[0]
    return rss_hrefs[0], atom_hrefs[0], json_url


@pytest.mark.parametrize(
    "raw",
    [
        "https://user:pass@example.com/feeds",
        "https://user@example.com/feeds",
    ],
)
def test_aud_006_rejects_userinfo(raw: str) -> None:
    with pytest.raises(_INVALID, match="userinfo"):
        Settings.model_validate({"public_base_url": raw})


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/feeds?x=1",
        "https://example.com/feeds/?utm=source",
    ],
)
def test_aud_006_rejects_query(raw: str) -> None:
    with pytest.raises(_INVALID, match="query"):
        Settings.model_validate({"public_base_url": raw})


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/feeds#top",
        "https://example.com/feeds/#frag",
    ],
)
def test_aud_006_rejects_fragment(raw: str) -> None:
    with pytest.raises(_INVALID, match="fragment"):
        Settings.model_validate({"public_base_url": raw})


def test_aud_006_trailing_slash_canonical_self_urls() -> None:
    slash = _self_hrefs("https://example.com/pg-feeds/")
    noslash = _self_hrefs("https://example.com/pg-feeds")
    assert slash == noslash
    assert slash == (
        "https://example.com/pg-feeds/rss.xml",
        "https://example.com/pg-feeds/atom.xml",
        "https://example.com/pg-feeds/feed.json",
    )
    assert feed_self_url(slash[2], kind="rss") == slash[0]
    assert feed_self_url(slash[2], kind="atom") == slash[1]
    assert feed_self_url(slash[2], kind="json") == slash[2]


def test_aud_006_unicode_host_idna() -> None:
    settings = Settings.model_validate({"public_base_url": "https://münchen.example.com/feeds"})
    assert settings.public_base_url == "https://xn--mnchen-3ya.example.com/feeds/"
    hrefs = _self_hrefs("https://münchen.example.com/feeds/")
    assert hrefs == (
        "https://xn--mnchen-3ya.example.com/feeds/rss.xml",
        "https://xn--mnchen-3ya.example.com/feeds/atom.xml",
        "https://xn--mnchen-3ya.example.com/feeds/feed.json",
    )
    assert all("münchen" not in href for href in hrefs)
    assert feed_self_url("https://münchen.example.com/feeds/feed.json", kind="rss") == hrefs[0]
