"""CLI ``check`` must treat truncated/unclosed RSS as UNPARSEABLE_XML.

Codex once claimed ``check`` counted a truncated RSS body as 233 items.
Deep verify maps ``ET.ParseError`` to ``UNPARSEABLE_XML``; this lock runs the
CLI path (both feed triples + required ``catalog.json``).
"""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from paul_graham_essay_feeds.catalog import save_catalog
from paul_graham_essay_feeds.cli import app
from paul_graham_essay_feeds.feeds import render_snapshot_feeds, write_feeds
from paul_graham_essay_feeds.models import (
    Catalog,
    CatalogEntry,
    FeedEntrySnapshot,
    FeedSnapshot,
    utc_now,
)

runner = CliRunner()

_COUNT_CLAIM = re.compile(
    r"(COUNT_MISMATCH|\bVALID\b \d+ items|RSS=\d+|Atom=\d+|JSON=\d+|\b\d+\s+items\b)"
)


def _seed_well_formed_bundle(repo_root: Path) -> bytes:
    now = utc_now()
    snapshot = FeedSnapshot(
        logical_updated_at=now,
        generator="pg-essay-feeds/test",
        items=[
            FeedEntrySnapshot(
                id="https://paulgraham.com/a.html",
                url="https://paulgraham.com/a.html",
                title="A",
                summary="Short summary for truncated XML check tests.",
                observed_updated_at=now,
            ),
        ],
    )
    rss, atom, jf = render_snapshot_feeds(snapshot)
    write_feeds(
        repo_root,
        rss=rss,
        atom=atom,
        json_feed=jf,
        simple_rss=rss,
        simple_atom=atom,
        simple_json_feed=jf,
    )
    save_catalog(
        repo_root / "catalog.json",
        Catalog(
            schema_version=2,
            material_config_fingerprint="test",
            entry_order=["https://paulgraham.com/a.html"],
            entries={
                "https://paulgraham.com/a.html": CatalogEntry(
                    stable_id="https://paulgraham.com/a.html",
                    url="https://paulgraham.com/a.html",
                    title="A",
                    position=0,
                    first_seen_at=now,
                    last_seen_at=now,
                    observed_updated_at=now,
                    summary="Short summary for truncated XML check tests.",
                )
            },
        ),
    )
    return rss


def _cli_text(result: object) -> str:
    output = getattr(result, "output", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    parts = [output, stderr]
    exc: BaseException | None = getattr(result, "exception", None)
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts.append(str(exc))
        nxt = exc.__cause__ or exc.__context__
        exc = nxt if nxt is not exc else None
    return "\n".join(parts)


def _assert_unparseable_check(repo_root: Path) -> None:
    result = runner.invoke(
        app,
        ["check", "--repo-root", str(repo_root), "--min-items", "1", "--quiet"],
    )
    text = _cli_text(result)
    assert result.exit_code == 2, text
    assert "UNPARSEABLE_XML" in text
    assert _COUNT_CLAIM.search(text) is None, text


def test_check_truncated_rss_mid_tag_is_unparseable(repo_root: Path) -> None:
    rss = _seed_well_formed_bundle(repo_root)
    needle = b"<description>"
    cut = rss.rfind(needle)
    assert cut != -1
    truncated = rss[: cut + len(b"<descrip")]
    (repo_root / "feeds" / "rss.xml").write_bytes(truncated)
    _assert_unparseable_check(repo_root)


def test_check_rss_missing_close_tags_is_unparseable(repo_root: Path) -> None:
    rss = _seed_well_formed_bundle(repo_root)
    text = rss.decode("utf-8")
    assert "</item>" in text
    unclosed = text.replace("</channel>", "").replace("</rss>", "")
    assert unclosed != text
    (repo_root / "feeds" / "rss.xml").write_text(unclosed, encoding="utf-8")
    _assert_unparseable_check(repo_root)
