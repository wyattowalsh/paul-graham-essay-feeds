"""Ops-set second transaction tests (RV-017)."""

from __future__ import annotations

import json
from pathlib import Path

from paul_graham_essay_feeds.build import _checksums_bytes, _json_bytes, _publish_ops
from paul_graham_essay_feeds.config import load_config


def test_publish_ops_writes_state_report_checksums(tmp_repo: Path) -> None:
    cfg = load_config(repo_root=tmp_repo, cli_overrides={"min_items": 1})
    # Seed generation files so checksums include them when present.
    cfg.path_rss.parent.mkdir(parents=True, exist_ok=True)
    cfg.path_rss.write_bytes(b"<rss/>\n")
    cfg.path_atom.write_bytes(b"<atom/>\n")
    cfg.path_json_feed.write_bytes(b"{}\n")
    cfg.path_opml.write_bytes(b"<opml/>\n")
    cfg.path_essays.parent.mkdir(parents=True, exist_ok=True)
    cfg.path_essays.write_bytes(b'{"items":[]}\n')

    state = {"schema_version": 1, "last_status": "checked"}
    report = {"status": "checked", "item_count": 0}
    written = _publish_ops(cfg, state=state, report=report)
    assert cfg.path_state in written
    assert cfg.path_validation in written
    assert cfg.path_checksums in written
    assert json.loads(cfg.path_state.read_text(encoding="utf-8"))["last_status"] == "checked"
    assert json.loads(cfg.path_validation.read_text(encoding="utf-8"))["status"] == "checked"
    sums = cfg.path_checksums.read_text(encoding="utf-8")
    assert "state.json" in sums
    assert "validation.json" in sums
    assert "rss.xml" in sums


def test_checksums_bytes_prefers_overrides(tmp_repo: Path) -> None:
    cfg = load_config(repo_root=tmp_repo, cli_overrides={"min_items": 1})
    cfg.path_rss.parent.mkdir(parents=True, exist_ok=True)
    cfg.path_rss.write_bytes(b"on-disk\n")
    overrides = {
        cfg.path_state: _json_bytes({"schema_version": 1}),
        cfg.path_validation: _json_bytes({"status": "ok"}),
    }
    payload = _checksums_bytes(cfg, overrides).decode("utf-8")
    assert "state.json" in payload
    assert "validation.json" in payload
    assert "rss.xml" in payload
