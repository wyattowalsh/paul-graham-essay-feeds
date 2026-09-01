"""Host Worker is a MIME wrapper over committed feeds/, not a second publisher."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_WORKER = _REPO / "host" / "src" / "worker.js"
_WRANGLER = _REPO / "host" / "wrangler.toml"


def test_worker_serves_typed_feeds_and_latest_projection() -> None:
    worker = _WORKER.read_text(encoding="utf-8")
    wrangler = _WRANGLER.read_text(encoding="utf-8")
    assert 'directory = "../feeds"' in wrangler
    assert "run_worker_first = true" in wrangler
    assert "application/rss+xml" in worker
    assert "application/atom+xml" in worker
    assert "application/feed+json" in worker
    assert "/latest/rss.xml" in worker
    assert "GET" in worker and "HEAD" in worker
    assert "access-control-allow-origin" in worker
    assert "raw.githubusercontent.com" in worker
    assert "site/" not in worker


def test_worker_js_parses_when_node_is_available() -> None:
    node = shutil.which("node")
    if node is None:
        return
    subprocess.run([node, "--check", str(_WORKER)], check=True)
