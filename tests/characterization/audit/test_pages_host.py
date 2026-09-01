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


def test_pages_follows_update_feeds_product_sha_not_workflow_head() -> None:
    """GITHUB_TOKEN pushes do not fire on.push; assemble the bot product SHA.

    ``workflow_run.head_sha`` is the pre-push Update-feeds source commit.
    """
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert 'workflows: ["Update feeds"]' in text
    assert "types: [completed]" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert "name: product-identity" in text
    assert "run-id: ${{ github.event.workflow_run.id }}" in text
    assert "ref: ${{ steps.identity.outputs.product_sha }}" in text
    assert "ref: ${{ github.event.workflow_run.head_sha }}" not in text
    assert "ref: main" not in text
    assert "product_sha must not be a mutable branch name" in text


def test_host_worker_is_gone() -> None:
    assert not (_REPO / "host").exists()
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    assert _HOST in readme
    assert "workers.dev" not in readme
    assert "wrangler" not in readme.lower()
