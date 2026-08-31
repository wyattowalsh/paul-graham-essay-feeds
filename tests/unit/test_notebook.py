"""Static notebook contract and status-parser characterization (PGF-P1-004)."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebook.ipynb"
FEED_NAMES = (
    "rss.xml",
    "atom.xml",
    "feed.json",
    "rss.simple.xml",
    "atom.simple.xml",
    "feed.simple.json",
)


def _generate_cell_source() -> str:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cells = notebook["cells"]
    generate = next(
        cell
        for cell in cells
        if "generate" in cell.get("metadata", {}).get("tags", [])
        or "ENRICH" in "".join(cell.get("source", []))
    )
    return "".join(generate.get("source", []))


def _load_live_check_parser():
    source = _generate_cell_source()
    start = source.index("REACH_TOKEN")
    end = source.index("update_argv")
    namespace: dict[str, object] = {"re": re}
    exec(source[start:end], namespace)
    parse = namespace["parse_live_check_report"]
    assert callable(parse)
    return parse


def test_notebook_static_contract() -> None:
    source = _generate_cell_source()
    assert "AUTO_DOWNLOAD = True" in source
    assert "@param" in source
    assert "if AUTO_DOWNLOAD" in source
    assert source.index("if AUTO_DOWNLOAD") < source.index("files.download")
    assert 'update_argv.append("--no-validate-links")' not in source
    assert "update_argv.append('--no-validate-links')" not in source
    assert '", "--no-validate-links"' not in source
    assert "', '--no-validate-links'" not in source
    assert 'update_argv.append("--all-pages")' not in source
    assert "update_argv.append('--all-pages')" not in source
    assert "PG_ESSAY_FEEDS_MAX_PAGE_FETCHES" in source
    assert "PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS" in source
    assert 'setdefault("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", "40")' in source
    assert 'setdefault("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", "40")' in source
    assert "from paul_graham_essay_feeds" not in source
    assert "import paul_graham_essay_feeds" not in source
    assert "uvx" in source
    assert 'pkg = "git+https://github.com/wyattowalsh/paul-graham-essay-feeds@main"' in source
    assert 'pkg = "git+https://github.com/wyattowalsh/paul-graham-essay-feeds@v0.2.0"' not in source
    assert 'pkg = "git+https://github.com/wyattowalsh/paul-graham-essay-feeds@v1.0.0"' not in source
    for name in FEED_NAMES:
        assert name in source
    assert "catalog.json" in source
    zip_block = source[source.index("ZipFile") : source.index("downloaded")]
    assert "catalog.json" not in zip_block
    for name in FEED_NAMES:
        assert name in zip_block or "arcname=name" in zip_block


def test_notebook_parser_dedicated_probe_failure() -> None:
    parse = _load_live_check_parser()
    log = (
        "PGF_REACHABILITY_FAIL https://paulgraham.com/a.html | probe | HTTP 503\n"
        "Link probe issue: https://paulgraham.com/a.html → HTTP 503\n"
    )
    ok, count, reach, degrade = parse(log)
    assert ok is False
    assert count >= 1
    assert any("a.html" in msg or "503" in msg for msg in reach)
    assert degrade == []


def test_notebook_parser_enrichment_transport_failure_no_probe() -> None:
    parse = _load_live_check_parser()
    log = "PGF_REACHABILITY_FAIL https://paulgraham.com/b.html | enrich_fetch | timeout\n"
    ok, count, reach, degrade = parse(log)
    assert ok is False
    assert count == 1
    assert any("enrich_fetch" in msg and "b.html" in msg for msg in reach)
    assert degrade == []


def test_notebook_parser_enrichment_parse_degradation() -> None:
    parse = _load_live_check_parser()
    log = "PGF_ENRICH_DEGRADED https://paulgraham.com/c.html | parse | malformed metadata\n"
    ok, count, reach, degrade = parse(log)
    assert ok is False
    assert count == 1
    assert reach == []
    assert any("parse" in msg and "c.html" in msg for msg in degrade)


def test_notebook_parser_success_is_green() -> None:
    parse = _load_live_check_parser()
    log = "Live link probes OK (3 urls)\nEnriched 3/3 essays with page metadata\n"
    ok, count, reach, degrade = parse(log)
    assert ok is True
    assert count == 0
    assert reach == []
    assert degrade == []
