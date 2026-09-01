"""GitHub Pages is the hosted subscribe surface (not a second publisher)."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_HOST = "https://wyattowalsh.github.io/paul-graham-essay-feeds/"
_WORKFLOW = _REPO / ".github" / "workflows" / "pages.yml"


def test_pages_workflow_deploys_assembled_artifact() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "paul_graham_essay_feeds.pages" in text
    assert "actions/upload-pages-artifact@" in text
    assert "actions/deploy-pages@" in text
    assert "include-hidden-files: true" in text
    assert "persist-credentials: false" in text
    ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "paul_graham_essay_feeds.pages" in ci
    assert "site/" not in text
    assert "host/" not in text


def test_host_worker_is_gone() -> None:
    assert not (_REPO / "host").exists()
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    assert _HOST in readme
    assert "workers.dev" not in readme
    assert "wrangler" not in readme.lower()
