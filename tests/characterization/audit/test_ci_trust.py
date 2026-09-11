"""WF-CI-TRUST owner: CI continuation, concurrency, and authorization artifacts."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CI = _REPO / ".github" / "workflows" / "ci.yml"


def test_ci_offline_smoke_is_not_this_file() -> None:
    """Ordinary push/PR quality steps remain; trust routes are additive."""
    text = _CI.read_text(encoding="utf-8")
    assert "--no-validate-links" in text
    assert "--no-enrich" in text
    assert "uv sync --locked --all-groups" in text
    assert "enable-cache: true" in text


def test_ci_has_update_feeds_continuation_on_main() -> None:
    text = _CI.read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert "Update feeds" in text
    assert "types: [completed]" in text
    assert "branches: [main]" in text
    assert "github.workflow_sha" in text
    assert "product_sha" in text
    assert "workflow_run.head_sha" not in text.split("Check out selected product")[1][:800]


def test_ci_concurrency_is_route_commit_or_run_keyed() -> None:
    text = _CI.read_text(encoding="utf-8")
    assert "ci-push-" in text
    assert "ci-update-" in text
    assert "ci-pr-" in text
    assert "github.event.workflow_run.id" in text
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text
    # Authorizing main groups must not share the PR cancel-in-progress slot.
    assert "CI-${{ github.event.pull_request.number || github.ref }}" not in text
    assert (
        "group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
        not in text
    )


def test_ci_emits_canonical_authorization_artifact() -> None:
    text = _CI.read_text(encoding="utf-8")
    assert "ci-authorization-" in text
    assert "build_ci_authorization_document" in text
    assert "ci-noop-" in text
    assert "run_quality" in text
    assert "product_identity.py" in text


def test_push_pending_and_late_continuation_use_distinct_groups() -> None:
    """A pending main push group cannot be replaced by a continuation group."""
    text = _CI.read_text(encoding="utf-8")
    assert "format('ci-push-{0}', github.sha)" in text
    assert "format('ci-update-{0}', github.event.workflow_run.id)" in text
    assert "format('ci-pr-{0}', github.event.pull_request.number)" in text
