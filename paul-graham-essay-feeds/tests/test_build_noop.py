"""No-op and missing-artifact behavior (RV-005)."""

from __future__ import annotations

from pathlib import Path

import pytest

from paul_graham_essay_feeds.build import run_update
from paul_graham_essay_feeds.config import load_config
from paul_graham_essay_feeds.domain import FeedError


def test_noop_missing_feed_fails(
    tmp_repo: Path,
    fixture_html: Path,
    public_base: str,
) -> None:
    cfg = load_config(
        repo_root=tmp_repo,
        config_path=tmp_repo / "config.toml",
        cli_overrides={"public_base_url": public_base, "min_items": 233},
    )
    assert (
        run_update(
            cfg,
            source_file=fixture_html,
            force=True,
            quiet=True,
        )
        == 0
    )
    # Remove one feed; signature still matches catalog → must fail closed.
    cfg.path_atom.unlink()
    with pytest.raises(FeedError, match="feed artifacts missing"):
        run_update(
            cfg,
            source_file=fixture_html,
            force=False,
            quiet=True,
        )
