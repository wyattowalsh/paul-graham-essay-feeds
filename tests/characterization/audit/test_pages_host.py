"""WF-PAGES owner: Pages is a serialized current-tip reconciler, not a publisher.

GitHub Pages is the hosted subscribe surface. Update wakeups are observer-only;
only a trusted CI authorization can deploy.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_HOST = "https://wyattowalsh.github.io/paul-graham-essay-feeds/"
_WORKFLOW = _REPO / ".github" / "workflows" / "pages.yml"


def _job_block(text: str, job: str) -> str:
    marker = f"\n  {job}:"
    start = text.find(marker)
    assert start >= 0, f"job {job!r} not found"
    rest = text[start + len(marker) :]
    match = re.search(r"\n  [A-Za-z][\w-]*:", rest)
    return rest[: match.start()] if match else rest


def test_pages_workflow_deploys_assembled_artifact() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "paul_graham_essay_feeds.pages" in text
    assert "actions/upload-pages-artifact@" in text
    assert "actions/deploy-pages@" in text
    assert "include-hidden-files: true" in text
    assert "retention-days: 3" in text
    assert "persist-credentials: false" in text
    assert "site/" not in text
    assert "host/" not in text


def test_pages_triggers_only_ci_and_update_workflow_run_on_main() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert "workflows: [CI, Update feeds]" in text or (
        "workflows:" in text and "CI" in text and "Update feeds" in text
    )
    assert "types: [completed]" in text
    assert "branches: [main]" in text
    assert "workflow_dispatch:" not in text
    assert "push:" not in text
    assert "name: product-identity\n" not in text
    assert "ref: ${{ github.event.workflow_run.head_sha }}" not in text
    assert "ref: main" not in text


def test_pages_update_route_is_observer_only() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "observe_update" in text
    observe = _job_block(text, "observe_update")
    assert "pages: write" not in observe
    assert "id-token: write" not in observe
    assert "actions/deploy-pages@" not in observe
    assert "actions/upload-pages-artifact@" not in observe
    assert "github-pages" not in observe


def test_pages_production_group_is_current_tip_only() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "pages-current-main-v1" in text
    assert "pages-observer-" in text
    assert "cancel-in-progress: false" in text
    assert "selected_sha" in text
    assert "github.sha" in text
    assert "ci-authorization-" in text


def test_host_worker_is_gone() -> None:
    assert not (_REPO / "host").exists()
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    assert _HOST in readme
    assert "workers.dev" not in readme
    assert "wrangler" not in readme.lower()
