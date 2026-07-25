"""Prevalidated feed publication (ADR-005 / F-008 / F-009 / F-013).

Verify bytes in memory, then stage and publish with explicit file modes.
Generation directories and atomic ``current.json`` switch arrive in Wave 2;
this module provides the safe publish primitive for the three feed files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from paul_graham_essay_feeds.feeds import write_feeds
from paul_graham_essay_feeds.model import FeedError
from paul_graham_essay_feeds.presentation import NULL_REPORTER, ProgressReporter
from paul_graham_essay_feeds.verification import VerificationReport, assert_verified


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """Outcome of a prevalidated write of the three feed artifacts."""

    root: Path
    rss_path: Path
    atom_path: Path
    json_path: Path
    report: VerificationReport


def publish_feed_bundle(
    root: Path,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    min_items: int,
    reporter: ProgressReporter | None = None,
    file_mode: int = 0o644,
) -> PublicationResult:
    """Verify feed bytes, then atomically-ish write under ``root/feeds/``.

    Raises :class:`FeedError` if verification fails **before** any replace.
    """
    progress = reporter or NULL_REPORTER
    report = assert_verified(
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        min_items=min_items,
    )
    write_feeds(
        root,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        reporter=progress,
        file_mode=file_mode,
    )
    feeds = root / "feeds"
    return PublicationResult(
        root=root,
        rss_path=feeds / "rss.xml",
        atom_path=feeds / "atom.xml",
        json_path=feeds / "feed.json",
        report=report,
    )


def publish_or_raise(
    root: Path,
    *,
    rss: bytes,
    atom: bytes,
    json_feed: bytes,
    min_items: int,
    reporter: ProgressReporter | None = None,
    file_mode: int = 0o644,
) -> PublicationResult:
    """Alias for :func:`publish_feed_bundle` (explicit fail-closed name)."""
    try:
        return publish_feed_bundle(
            root,
            rss=rss,
            atom=atom,
            json_feed=json_feed,
            min_items=min_items,
            reporter=reporter,
            file_mode=file_mode,
        )
    except FeedError:
        raise
