"""AUD-003: independent feed-level contracts (RSS/Atom/JSON Feed 1.1)."""

from __future__ import annotations

import json
from pathlib import Path

from paul_graham_essay_feeds.feeds import write_feeds
from paul_graham_essay_feeds.models import (
    FEED_ID,
    FEED_ID_SIMPLE,
)
from paul_graham_essay_feeds.models import (
    JSON_FEED_VERSION as JSON_FEED_VERSION_IRI,
)
from paul_graham_essay_feeds.verify import (
    ATOM_FEED_COUNT,
    ATOM_NAMESPACE,
    ATOM_REQUIRED_ELEMENT,
    FEED_CLOCK,
    FEED_ID_COLLISION,
    FEED_ROOT,
    ID_ORDER_MISMATCH,
    INVALID_TIMESTAMP,
    INVALID_URI,
    JSON_FEED_FIELD,
    JSON_FEED_VERSION,
    RSS_CHANNEL,
    RSS_VERSION,
    SELF_LINK_MISMATCH,
    VARIANT_IDENTITY,
    VerificationReport,
    verify_feed_bytes,
    verify_feed_dir,
)

_SUMMARY = "Short summary for essay A."
_ITEM_URL = "https://paulgraham.com/a.html"
_HOME = "https://paulgraham.com/articles.html"
_UPDATED = "2024-01-01T12:00:00Z"
_PUBDATE = "Mon, 01 Jan 2024 12:00:00 GMT"


def _codes(report: VerificationReport) -> set[str]:
    return {v.code for v in report.violations}


def _atom(*, kind: str = "enriched", extra_feed: str = "", extra_entry: str = "") -> bytes:
    feed_id = FEED_ID_SIMPLE if kind == "simple" else FEED_ID
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Paul Graham: Essays</title>
  <id>{feed_id}</id>
  <updated>{_UPDATED}</updated>
  <author><name>Paul Graham</name></author>
  {extra_feed}
  <entry>
    <title>A</title>
    <id>{_ITEM_URL}</id>
    <updated>{_UPDATED}</updated>
    <summary>{_SUMMARY}</summary>
    <link rel="alternate" href="{_ITEM_URL}"/>
    {extra_entry}
  </entry>
</feed>
""".encode()


def _rss(*, extra_channel: str = "", extra_item: str = "") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Paul Graham: Essays</title>
    <link>{_HOME}</link>
    <description>Unofficial metadata feeds</description>
    <lastBuildDate>{_PUBDATE}</lastBuildDate>
    {extra_channel}
    <item>
      <title>A</title>
      <link>{_ITEM_URL}</link>
      <guid>{_ITEM_URL}</guid>
      <description>{_SUMMARY}</description>
      {extra_item}
    </item>
  </channel>
</rss>
""".encode()


def _json_feed(
    *,
    extra: dict[str, object] | None = None,
    item_extra: dict[str, object] | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "version": JSON_FEED_VERSION_IRI,
        "title": "Paul Graham: Essays",
        "home_page_url": _HOME,
        "items": [
            {
                "id": _ITEM_URL,
                "url": _ITEM_URL,
                "title": "A",
                "summary": _SUMMARY,
                "content_text": _SUMMARY,
                "date_modified": _UPDATED,
                **(item_extra or {}),
            }
        ],
    }
    if extra:
        payload.update(extra)
    return (json.dumps(payload) + "\n").encode()


def _triple(*, kind: str = "enriched") -> tuple[bytes, bytes, bytes]:
    return _rss(), _atom(kind=kind), _json_feed()


def test_synthetic_triple_passes_feed_contract() -> None:
    rss, atom, jf = _triple()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert report.ok is True


def test_rss_wrong_root() -> None:
    rss, atom, jf = _triple()
    rss = b'<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert FEED_ROOT in _codes(report)


def test_rss_wrong_version() -> None:
    rss, atom, jf = _triple()
    rss = rss.replace(b'version="2.0"', b'version="0.91"', 1)
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert RSS_VERSION in _codes(report)


def test_rss_missing_channel_and_duplicate_channel() -> None:
    rss, atom, jf = _triple()
    missing = b'<?xml version="1.0"?><rss version="2.0"></rss>'
    report = verify_feed_bytes(rss=missing, atom=atom, json_feed=jf, min_items=1)
    assert RSS_CHANNEL in _codes(report)

    doubled = rss.replace(b"</channel>\n</rss>", b"</channel><channel></channel>\n</rss>", 1)
    report2 = verify_feed_bytes(rss=doubled, atom=atom, json_feed=jf, min_items=1)
    assert RSS_CHANNEL in _codes(report2)


def test_rss_missing_channel_title() -> None:
    rss, atom, jf = _triple()
    rss = rss.replace(b"<title>Paul Graham: Essays</title>\n    <link>", b"<link>", 1)
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert RSS_CHANNEL in _codes(report)


def test_atom_wrong_namespace() -> None:
    rss, atom, jf = _triple()
    atom = atom.replace(
        b'xmlns="http://www.w3.org/2005/Atom"',
        b'xmlns="http://www.w3.org/2005/AtomX"',
        1,
    )
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert ATOM_NAMESPACE in _codes(report)


def test_atom_feed_count_nested() -> None:
    rss, _, jf = _triple()
    atom = _atom(
        extra_feed='<feed xmlns="http://www.w3.org/2005/Atom"><title>nested</title></feed>'
    )
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert ATOM_FEED_COUNT in _codes(report)


def test_atom_author_requires_name() -> None:
    rss, atom, jf = _triple()
    atom = atom.replace(b"<author><name>Paul Graham</name></author>", b"<author></author>", 1)
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert ATOM_REQUIRED_ELEMENT in _codes(report)


def test_json_feed_url_must_be_string() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf)
    payload["feed_url"] = ["https://example.com/feeds/feed.json"]
    jf = (json.dumps(payload) + "\n").encode()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert JSON_FEED_FIELD in _codes(report)


def test_atom_missing_required_feed_id() -> None:
    rss, atom, jf = _triple()
    atom = atom.replace(f"<id>{FEED_ID}</id>".encode(), b"", 1)
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert ATOM_REQUIRED_ELEMENT in _codes(report)


def test_json_wrong_version() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf)
    payload["version"] = "https://jsonfeed.org/version/1"
    jf = (json.dumps(payload) + "\n").encode()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert JSON_FEED_VERSION in _codes(report)


def test_json_missing_title_and_home() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf)
    del payload["title"]
    del payload["home_page_url"]
    jf = (json.dumps(payload) + "\n").encode()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert JSON_FEED_FIELD in _codes(report)


def test_invalid_uri_on_item_link() -> None:
    rss, atom, jf = _triple()
    rss = rss.replace(
        f"<link>{_ITEM_URL}</link>".encode(),
        b"<link>not-a-uri</link>",
        1,
    )
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert INVALID_URI in _codes(report)


def test_invalid_timestamp_pubdate_and_json_date() -> None:
    rss, atom, jf = _triple()
    rss = rss.replace(
        b"<description>Short summary for essay A.</description>",
        b"<description>Short summary for essay A.</description><pubDate>yesterday</pubDate>",
        1,
    )
    payload = json.loads(jf)
    payload["items"][0]["date_modified"] = "soon"
    jf = (json.dumps(payload) + "\n").encode()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert INVALID_TIMESTAMP in _codes(report)


def test_self_link_mismatch_is_exact_not_substring() -> None:
    rss = _rss(
        extra_channel=(
            '<atom:link rel="self" type="application/rss+xml" '
            'href="https://example.com/feeds/rss.xml"/>'
        )
    )
    atom = _atom(
        extra_feed=(
            '<link rel="self" type="application/atom+xml" '
            'href="https://example.com/feeds/atom.xml"/>'
        )
    )
    jf = _json_feed(extra={"feed_url": "https://example.com/feeds/feed.json"})
    good = verify_feed_bytes(
        rss=rss,
        atom=atom,
        json_feed=jf,
        min_items=1,
        public_base_url="https://example.com/feeds",
    )
    assert good.ok is True

    # Substring / prefix of the expected URL must not count as a match.
    bad_rss = rss.replace(
        b"https://example.com/feeds/rss.xml",
        b"https://example.com/feeds/rss.xml.backup",
        1,
    )
    report = verify_feed_bytes(
        rss=bad_rss,
        atom=atom,
        json_feed=jf,
        min_items=1,
        public_base_url="https://example.com/feeds",
    )
    assert SELF_LINK_MISMATCH in _codes(report)

    report2 = verify_feed_bytes(
        rss=rss,
        atom=atom,
        json_feed=jf,
        min_items=1,
        expected_self={"rss": "https://other.example/rss.xml"},
    )
    assert SELF_LINK_MISMATCH in _codes(report2)


def test_invalid_self_uri_without_expected() -> None:
    rss, atom, jf = _triple()
    rss = _rss(
        extra_channel='<atom:link rel="self" href="not a url"/>',
    )
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert INVALID_URI in _codes(report)


def test_variant_identity_enriched_must_not_use_simple_id() -> None:
    rss, _a, jf = _triple(kind="enriched")
    atom = _atom(kind="simple")
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1, kind="enriched")
    assert VARIANT_IDENTITY in _codes(report)


def test_variant_identity_simple_must_not_use_enriched_id(tmp_path: Path) -> None:
    rss_e, _atom_e, jf_e = _triple(kind="enriched")
    rss_s, _atom_s, jf_s = _triple(kind="simple")
    write_feeds(
        tmp_path,
        rss=rss_e,
        atom=_atom(kind="simple"),
        json_feed=jf_e,
        simple_rss=rss_s,
        simple_atom=_atom(kind="enriched"),
        simple_json_feed=jf_s,
    )
    report = verify_feed_dir(tmp_path, min_items=1)
    assert VARIANT_IDENTITY in _codes(report)
    assert FEED_ID_COLLISION not in _codes(report)


def test_feed_clock_unparseable_last_build_and_updated() -> None:
    rss, atom, jf = _triple()
    rss = rss.replace(_PUBDATE.encode(), b"not-a-date", 1)
    atom = atom.replace(_UPDATED.encode(), b"not-a-date", 1)
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert FEED_CLOCK in _codes(report)


def test_feed_id_collision_atom_and_json(tmp_path: Path) -> None:
    rss_e, atom_e, jf_e = _triple(kind="enriched")
    rss_s, atom_s, jf_s = _triple(kind="simple")
    colliding_atom = atom_s.replace(FEED_ID_SIMPLE.encode(), FEED_ID.encode(), 1)
    jf_e = _json_feed(extra={"feed_url": "https://example.com/feeds/feed.json"})
    jf_s = _json_feed(extra={"feed_url": "https://example.com/feeds/feed.json"})
    write_feeds(
        tmp_path,
        rss=rss_e,
        atom=atom_e,
        json_feed=jf_e,
        simple_rss=rss_s,
        simple_atom=colliding_atom,
        simple_json_feed=jf_s,
    )
    report = verify_feed_dir(tmp_path, min_items=1)
    codes = _codes(report)
    assert FEED_ID_COLLISION in codes
    assert VARIANT_IDENTITY in codes


def test_catalog_order_extended_to_all_six_files(tmp_path: Path) -> None:
    rss_e, atom_e, jf_e = _triple(kind="enriched")
    rss_s, atom_s, jf_s = _triple(kind="simple")
    write_feeds(
        tmp_path,
        rss=rss_e,
        atom=atom_e,
        json_feed=jf_e,
        simple_rss=rss_s,
        simple_atom=atom_s,
        simple_json_feed=jf_s,
    )
    (tmp_path / "catalog.json").write_text(
        json.dumps({"entry_order": ["https://paulgraham.com/other.html"]}),
        encoding="utf-8",
    )
    report = verify_feed_dir(tmp_path, min_items=1)
    assert ID_ORDER_MISMATCH in _codes(report)
    paths = {v.path for v in report.violations if v.code == ID_ORDER_MISMATCH}
    assert paths == {
        "feeds/rss.xml",
        "feeds/atom.xml",
        "feeds/feed.json",
        "feeds/rss.simple.xml",
        "feeds/atom.simple.xml",
        "feeds/feed.simple.json",
    }
