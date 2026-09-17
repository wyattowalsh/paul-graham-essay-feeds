"""PGF-AUDIT-003 / RV-C-005 / RV-C-002: strict RFC3339, bodies, simple Atom id."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from paul_graham_essay_feeds.catalog import save_catalog
from paul_graham_essay_feeds.feeds import (
    catalog_to_feed_snapshot,
    render_atom,
    render_json,
    render_rss,
    write_feeds,
)
from paul_graham_essay_feeds.models import (
    FEED_ID,
    GENERATOR,
    Catalog,
    CatalogEntry,
    FeedEntrySnapshot,
    FeedSnapshot,
)
from paul_graham_essay_feeds.verify import (
    CATALOG_FIELD_MISMATCH,
    CLOCK_MISMATCH,
    FEED_CLOCK,
    FORBIDDEN_CONTENT,
    INVALID_TIMESTAMP,
    VARIANT_IDENTITY,
    VerificationReport,
    verify_bundle,
    verify_feed_bytes,
)

T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
_SID = "https://paulgraham.com/a.html"
_SUMMARY = "Short summary for essay A."


def _codes(report: VerificationReport) -> set[str]:
    return {v.code for v in report.violations}


def _snap() -> FeedSnapshot:
    return FeedSnapshot(
        logical_updated_at=T0,
        generator=GENERATOR,
        items=[
            FeedEntrySnapshot(
                id=_SID,
                url=_SID,
                title="A",
                summary=_SUMMARY,
                observed_updated_at=T0,
            )
        ],
    )


def _triple(*, variant: Literal["enriched", "simple"] = "enriched") -> tuple[bytes, bytes, bytes]:
    snap = _snap().model_copy(update={"variant": variant})
    return render_rss(snap), render_atom(snap), render_json(snap)


def _catalog(*, observed: datetime = T0) -> Catalog:
    return Catalog(
        schema_version=2,
        material_config_fingerprint="test",
        entry_order=[_SID],
        entries={
            _SID: CatalogEntry(
                stable_id=_SID,
                url=_SID,
                title="A",
                position=0,
                first_seen_at=T0,
                last_seen_at=T0,
                observed_updated_at=observed,
                summary=_SUMMARY,
            )
        },
    )


def _write_bundle(tmp_path: Path, catalog: Catalog) -> None:
    save_catalog(tmp_path / "catalog.json", catalog)
    snap = catalog_to_feed_snapshot(catalog, generator=GENERATOR, summary_mode="enriched")
    simple = catalog_to_feed_snapshot(catalog, generator=GENERATOR, summary_mode="title_only")
    rss, atom, jf = render_rss(snap), render_atom(snap), render_json(snap)
    sr, sa, sj = render_rss(simple), render_atom(simple), render_json(simple)
    write_feeds(
        tmp_path,
        rss=rss,
        atom=atom,
        json_feed=jf,
        simple_rss=sr,
        simple_atom=sa,
        simple_json_feed=sj,
    )


def test_json_date_modified_year_mutation_is_clock_mismatch() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["date_modified"] = "2001-01-01T12:00:00Z"
    jf = (json.dumps(payload) + "\n").encode()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert report.ok is False
    assert CLOCK_MISMATCH in _codes(report)


def test_atom_entry_updated_year_mutation_is_clock_mismatch() -> None:
    rss, atom, jf = _triple()
    year_2002 = b"<updated>2002-01-01T12:00:00Z</updated>"
    year_2024 = b"<updated>2024-01-01T12:00:00Z</updated>"
    # Second <updated> is the entry clock (feed-level is first).
    atom = atom.replace(year_2024, year_2002, 2).replace(year_2002, year_2024, 1)
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert report.ok is False
    assert CLOCK_MISMATCH in _codes(report)


def test_space_separated_atom_datetime_is_rejected() -> None:
    rss, atom, jf = _triple()
    atom = atom.replace(b"2024-01-01T12:00:00Z", b"2024-01-01 12:00:00Z", 1)
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert report.ok is False
    assert FEED_CLOCK in _codes(report) or INVALID_TIMESTAMP in _codes(report)


def test_catalog_observed_updated_at_divergence(tmp_path: Path) -> None:
    _write_bundle(tmp_path, _catalog(observed=T0))
    diverged = _catalog(observed=datetime(2003, 1, 1, 12, 0, 0, tzinfo=UTC))
    save_catalog(tmp_path / "catalog.json", diverged)
    report = verify_bundle(tmp_path, min_items=1)
    assert report.ok is False
    assert CATALOG_FIELD_MISMATCH in _codes(report)


def test_offset_and_fractional_rfc3339_are_accepted() -> None:
    rss, atom, jf = _triple()
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["date_modified"] = "2024-01-01T12:00:00.000+00:00"
    jf = (json.dumps(payload) + "\n").encode()
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1)
    assert report.ok is True


def test_forbidden_bodies_and_epoch_feed_clock() -> None:
    rss, atom, jf = _triple()
    rss_body = rss.replace(
        b"</item>",
        b'<content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        b"full</content:encoded></item>",
        1,
    )
    assert FORBIDDEN_CONTENT in _codes(
        verify_feed_bytes(rss=rss_body, atom=atom, json_feed=jf, min_items=1)
    )
    atom_body = atom.replace(b"</entry>", b"<content>full</content></entry>", 1)
    assert FORBIDDEN_CONTENT in _codes(
        verify_feed_bytes(rss=rss, atom=atom_body, json_feed=jf, min_items=1)
    )
    payload = json.loads(jf.decode("utf-8"))
    payload["items"][0]["content_html"] = "<p>full</p>"
    jf_body = (json.dumps(payload) + "\n").encode()
    assert FORBIDDEN_CONTENT in _codes(
        verify_feed_bytes(rss=rss, atom=atom, json_feed=jf_body, min_items=1)
    )
    epoch_atom = atom.replace(
        b"<updated>2024-01-01T12:00:00Z</updated>",
        b"<updated>1970-01-01T00:00:00Z</updated>",
        1,
    )
    assert FEED_CLOCK in _codes(
        verify_feed_bytes(rss=rss, atom=epoch_atom, json_feed=jf, min_items=1)
    )


def test_simple_kind_does_not_carve_out_enriched_feed_id() -> None:
    rss, _atom_s, jf = _triple(variant="simple")
    atom = render_atom(_snap())
    assert FEED_ID.encode() in atom
    report = verify_feed_bytes(rss=rss, atom=atom, json_feed=jf, min_items=1, kind="simple")
    assert report.ok is False
    assert VARIANT_IDENTITY in _codes(report)
