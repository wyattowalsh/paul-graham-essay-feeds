"""Unit tests for deep feed verification (one-fault fixtures)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from paul_graham_essay_feeds.feeds import (
    render_atom,
    render_json,
    render_rss,
    write_feeds,
)
from paul_graham_essay_feeds.models import (
    FEED_SUMMARY_CHARS,
    Essay,
    FeedEntrySnapshot,
    FeedError,
    FeedSnapshot,
)
from paul_graham_essay_feeds.verify import (
    BELOW_MIN_ITEMS,
    CONTENT_TEXT_MISMATCH,
    COUNT_MISMATCH,
    DUPLICATE_ID,
    EMPTY_SUMMARY,
    EMPTY_TITLE,
    EMPTY_URL,
    ID_ORDER_MISMATCH,
    MISSING_FILE,
    SUMMARY_LENGTH,
    UNICODE_REPLACEMENT,
    UNPARSEABLE_JSON,
    UNPARSEABLE_XML,
    VerificationReport,
    assert_verified,
    raise_on_failure,
    verify_feed_bytes,
    verify_feed_dir,
)

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


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


def _snapshot(essays: list[Essay] | None = None) -> FeedSnapshot:
    essays = essays if essays is not None else _sample()
    return FeedSnapshot(
        logical_updated_at=T0,
        generator="pg-essay-feeds/test",
        items=[
            FeedEntrySnapshot(
                id=e.stable_id,
                url=e.url,
                title=e.title,
                summary=e.summary or e.title,
                observed_updated_at=T0,
                published_at=e.published_at,
            )
            for e in essays
        ],
    )


def _good_triple(essays: list[Essay] | None = None) -> tuple[bytes, bytes, bytes]:
    snap = _snapshot(essays)
    return render_rss(snap), render_atom(snap), render_json(snap)


def _codes(report: VerificationReport) -> list[str]:
    return [v.code for v in report.violations]


def test_verify_feed_bytes_happy_path() -> None:
    rss, atom, jf = _good_triple()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is True
    assert report.violations == []


def test_unicode_replacement_fffd_in_summary() -> None:
    """F-003: U+FFFD in summary/title → UNICODE_REPLACEMENT."""
    rss, atom, jf = _good_triple()
    payload = json.loads(jf.decode("utf-8"))
    bad = "Broken \ufffd summary"
    payload["items"][0]["summary"] = bad
    payload["items"][0]["content_text"] = bad
    jf = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is False
    assert UNICODE_REPLACEMENT in _codes(report)
    assert any(v.index == 0 for v in report.violations if v.code == UNICODE_REPLACEMENT)


def test_count_mismatch() -> None:
    rss, atom, jf = _good_triple()
    # Drop one RSS item so counts diverge (one fault).
    rss_text = rss.decode("utf-8")
    rss_text = re.sub(
        r"<item>.*?</item>",
        "",
        rss_text,
        count=1,
        flags=re.DOTALL,
    )
    rss = rss_text.encode("utf-8")

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert report.ok is False
    assert COUNT_MISMATCH in _codes(report)


def test_below_min_items() -> None:
    rss, atom, jf = _good_triple()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=10)
    assert report.ok is False
    assert BELOW_MIN_ITEMS in _codes(report)
    assert any("below floor 10" in v.message for v in report.violations)


def test_content_text_not_equal_summary() -> None:
    rss, atom, jf = _good_triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["content_text"] = "does not match summary"
    jf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is False
    assert CONTENT_TEXT_MISMATCH in _codes(report)
    assert any(
        v.code == CONTENT_TEXT_MISMATCH and "must equal summary" in v.message
        for v in report.violations
    )


def test_duplicate_ids() -> None:
    """Duplicate stable ids in all three formats (single logical fault)."""
    essays = _sample()
    # Force identical stable_id across two essays so renderers stay in parity.
    dup = essays[1].model_copy(
        update={
            "stable_id": essays[0].stable_id,
            "url": essays[0].url,
            "is_permalink": essays[0].is_permalink,
        }
    )
    rss, atom, jf = _good_triple([essays[0], dup])

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is False
    assert DUPLICATE_ID in _codes(report)
    # Ordered ids still match across formats; only uniqueness fails.
    assert "ID_ORDER_MISMATCH" not in _codes(report)


def test_verify_feed_dir_happy_and_missing(tmp_path: Path) -> None:
    essays = _sample()
    snap = _snapshot(essays)
    write_feeds(
        tmp_path,
        rss=render_rss(snap),
        atom=render_atom(snap),
        json_feed=render_json(snap),
    )
    ok = verify_feed_dir(tmp_path, min_items=2)
    assert ok.ok is True

    empty = tmp_path / "empty"
    empty.mkdir()
    bad = verify_feed_dir(empty, min_items=1)
    assert bad.ok is False
    assert MISSING_FILE in _codes(bad)
    assert len(bad.violations) == 3


def test_raise_on_failure_and_assert_verified() -> None:
    rss, atom, jf = _good_triple()
    good = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    raise_on_failure(good)  # no raise
    assert_verified(rss=rss, atom=atom, json_feed=jf, min_items=2)

    bad = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=99)
    with pytest.raises(FeedError, match="BELOW_MIN_ITEMS"):
        raise_on_failure(bad)


def test_unparseable_xml_rss_and_atom() -> None:
    rss, atom, jf = _good_triple()
    report = verify_feed_bytes(
        rss=b"<not-xml",
        atom=atom,
        json_feed=jf,
        min_items=1,
    )
    assert report.ok is False
    assert UNPARSEABLE_XML in _codes(report)
    assert any(v.path == "feeds/rss.xml" for v in report.violations)

    report2 = verify_feed_bytes(
        rss=rss,
        atom=b"<feed><broken",
        json_feed=jf,
        min_items=1,
    )
    assert report2.ok is False
    assert UNPARSEABLE_XML in _codes(report2)
    assert any(v.path == "feeds/atom.xml" for v in report2.violations)


def test_unparseable_json_variants() -> None:
    rss, atom, _jf = _good_triple()

    # Invalid JSON text.
    r1 = verify_feed_bytes(rss=rss, atom=atom, json_feed=b"{not-json", min_items=1)
    assert UNPARSEABLE_JSON in _codes(r1)

    # Non-UTF-8 bytes.
    r2 = verify_feed_bytes(rss=rss, atom=atom, json_feed=b"\xff\xfe", min_items=1)
    assert UNPARSEABLE_JSON in _codes(r2)

    # Root not an object.
    r3 = verify_feed_bytes(rss=rss, atom=atom, json_feed=b"[1,2,3]\n", min_items=1)
    assert UNPARSEABLE_JSON in _codes(r3)
    assert any("root must be an object" in v.message for v in r3.violations)

    # Missing items array.
    r4 = verify_feed_bytes(
        rss=rss,
        atom=atom,
        json_feed=b'{"version":"https://jsonfeed.org/version/1.1"}\n',
        min_items=1,
    )
    assert UNPARSEABLE_JSON in _codes(r4)
    assert any("missing items" in v.message for v in r4.violations)

    # items[i] not an object.
    r5 = verify_feed_bytes(
        rss=rss,
        atom=atom,
        json_feed=b'{"items":["x"]}\n',
        min_items=1,
    )
    assert UNPARSEABLE_JSON in _codes(r5)
    assert any(v.index == 0 for v in r5.violations if v.code == UNPARSEABLE_JSON)


def test_empty_title_url_summary() -> None:
    rss, atom, jf = _good_triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["title"] = "   "
    payload["items"][0]["url"] = ""
    payload["items"][0]["summary"] = ""
    payload["items"][0]["content_text"] = ""
    jf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    codes = _codes(report)
    assert EMPTY_TITLE in codes
    assert EMPTY_URL in codes
    assert EMPTY_SUMMARY in codes


def test_summary_length_and_content_text_missing() -> None:
    rss, atom, jf = _good_triple()
    payload = json.loads(jf.decode("utf-8"))
    too_long = "x" * (FEED_SUMMARY_CHARS + 10)
    payload["items"][0]["summary"] = too_long
    payload["items"][0]["content_text"] = too_long
    jf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert SUMMARY_LENGTH in _codes(report)

    # Missing content_text on JSON item (keep RSS/Atom triple consistent).
    rss2, atom2, jf2_src = _good_triple()
    payload2 = json.loads(jf2_src.decode("utf-8"))
    del payload2["items"][0]["content_text"]
    jf2 = (json.dumps(payload2, indent=2) + "\n").encode("utf-8")
    report2 = verify_feed_bytes(rss=rss2, atom=atom2, json_feed=jf2, min_items=1)
    assert CONTENT_TEXT_MISMATCH in _codes(report2)
    assert any("requires string content_text" in v.message for v in report2.violations)


def test_unicode_replacement_in_title_and_content_text() -> None:
    rss, atom, jf = _good_triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["title"] = "Title with \ufffd mark"
    # Keep summary clean so only title + content_text paths fire distinctly.
    payload["items"][0]["content_text"] = payload["items"][0]["summary"] + "\ufffd"
    # content_text != summary also, but UNICODE_REPLACEMENT on content_text still recorded.
    jf = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert UNICODE_REPLACEMENT in _codes(report)
    assert any(v.code == UNICODE_REPLACEMENT and "title" in v.message for v in report.violations)
    assert any(
        v.code == UNICODE_REPLACEMENT and "content_text" in v.message for v in report.violations
    )


def test_id_order_mismatch_across_formats() -> None:
    essays = _sample()
    rss, atom, jf = _good_triple(essays)
    # Swap JSON item order so ordered ids diverge while counts match.
    payload = json.loads(jf.decode("utf-8"))
    payload["items"] = list(reversed(payload["items"]))
    jf = (json.dumps(payload, indent=2) + "\n").encode("utf-8")

    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is False
    assert ID_ORDER_MISMATCH in _codes(report)


def test_assert_verified_root_and_mode_errors(tmp_path: Path) -> None:
    essays = _sample()
    snap = _snapshot(essays)
    write_feeds(
        tmp_path,
        rss=render_rss(snap),
        atom=render_atom(snap),
        json_feed=render_json(snap),
    )
    report = assert_verified(root=tmp_path, min_items=2)
    assert report.ok is True

    rss, atom, jf = _good_triple()
    with pytest.raises(FeedError, match="either root or feed bytes"):
        assert_verified(root=tmp_path, rss=rss, atom=atom, json_feed=jf, min_items=2)

    with pytest.raises(FeedError, match="require rss, atom, and json_feed"):
        assert_verified(rss=rss, atom=None, json_feed=jf, min_items=2)


def test_raise_on_failure_suffix_for_many_violations() -> None:
    """When more than 5 violations exist, message includes (+N more)."""
    # Build many empty-field faults on JSON only (still parses).
    items = [
        {
            "id": f"https://paulgraham.com/e{i}.html",
            "url": "",
            "title": "",
            "summary": "",
            "content_text": "",
        }
        for i in range(4)
    ]
    jf = json.dumps({"version": "https://jsonfeed.org/version/1.1", "items": items}).encode()
    # Minimal valid-ish RSS/Atom with matching count so parity checks run too.
    rss = (
        '<?xml version="1.0"?><rss><channel>'
        + "".join(
            f"<item><guid>https://paulgraham.com/e{i}.html</guid>"
            f"<title></title><link></link><description></description></item>"
            for i in range(4)
        )
        + "</channel></rss>"
    ).encode()
    atom = (
        '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
        + "".join(
            f"<entry><id>https://paulgraham.com/e{i}.html</id>"
            f"<title></title><link href=''/><summary></summary></entry>"
            for i in range(4)
        )
        + "</feed>"
    ).encode()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert len(report.violations) > 5
    with pytest.raises(FeedError, match=r"\+\d+ more"):
        raise_on_failure(report)


def test_atom_link_fallback_without_alternate_rel() -> None:
    """Non-alternate Atom link rel still yields a url via href fallback."""
    essays = _sample()
    rss, atom, jf = _good_triple(essays)
    # Entry links use rel="related" so the first alternate pass misses;
    # the second loop returns the bare href.
    atom_text = atom.decode("utf-8")
    atom_text = re.sub(
        r'rel="alternate"([^>]*href="https://paulgraham\.com/a\.html")',
        r'rel="related"\1',
        atom_text,
        count=1,
    )
    atom = atom_text.encode("utf-8")
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=2)
    assert report.ok is True


def test_verify_feed_dir_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    essays = _sample()
    snap = _snapshot(essays)
    write_feeds(
        tmp_path,
        rss=render_rss(snap),
        atom=render_atom(snap),
        json_feed=render_json(snap),
    )
    feeds = tmp_path / "feeds"
    real_read = Path.read_bytes

    def boom(self: Path) -> bytes:
        if self.name == "rss.xml":
            raise OSError("permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", boom)
    report = verify_feed_dir(tmp_path, min_items=1)
    assert report.ok is False
    assert MISSING_FILE in _codes(report)
    assert any("Unreadable" in v.message for v in report.violations)
    # Silence unused local (feeds dir must exist for the monkeypatch path).
    assert feeds.is_dir()
