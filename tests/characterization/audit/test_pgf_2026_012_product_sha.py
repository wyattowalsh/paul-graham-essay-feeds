"""WF-UPD owner: Update feeds producer + Verify product trust/recovery.

Pages and CI assertions live in test_pages_host.py and test_ci_trust.py.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO / ".github" / "workflows"
_HELPER = _REPO / ".github" / "scripts" / "product_identity.py"

_SEVEN = (
    "catalog.json",
    "feeds/rss.xml",
    "feeds/atom.xml",
    "feeds/feed.json",
    "feeds/rss.simple.xml",
    "feeds/atom.simple.xml",
    "feeds/feed.simple.json",
)

_NEXT_JOB = re.compile(r"\n  [A-Za-z][\w-]*:")


def _job_block(text: str, job: str) -> str:
    marker = f"\n  {job}:"
    start = text.find(marker)
    assert start >= 0, f"job {job!r} not found"
    rest = text[start + len(marker) :]
    match = _NEXT_JOB.search(rest)
    return rest[: match.start()] if match else rest


def test_update_source_sha_is_proven_event_head() -> None:
    """PGF-FINAL-002: source_sha is HEAD after proving it equals GITHUB_SHA."""
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    update = _job_block(text, "update")
    assert "ref: ${{ github.sha }}" in update
    assert "source_sha=${head}" in update
    assert "FETCH_HEAD" not in update.split("Write product identity (no-op)", 1)[0]
    overlays = re.findall(r"git checkout[^\n]+catalog\.json feeds", text)
    assert overlays == ['git checkout "${product_sha}" -- catalog.json feeds']


def test_helper_is_stdlib_product_identity() -> None:
    text = _HELPER.read_text(encoding="utf-8")
    assert "SCHEMA_VERSION" in text
    assert "expected_artifact_name" in text
    assert "build_push_argv" in text
    assert "shell=True" not in text.replace("never shell=True", "")


def test_publish_gates_downloaded_candidate_not_source_checkout() -> None:
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    publish = _job_block(text, "publish")
    download = publish.split("Download updated workspace", 1)[1]
    download_step = download.split("\n      - name:", 1)[0]
    assert "name: feed-update-workspace" in download_step
    assert "path: ${{ runner.temp }}/candidate" in download_step
    assert "path: ${{ github.workspace }}" not in download_step
    gate = publish.split("Gate publish on downloaded seven files", 1)[1]
    for rel in _SEVEN:
        assert rel in gate
    assert "validate_candidate_tree" in publish
    assert "Validate downloaded feeds" in publish
    assert "uv run pg-essay-feeds check" in publish


def test_publish_uses_helper_identity_and_forbids_force() -> None:
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    publish = _job_block(text, "publish")
    assert "product_identity.py" in publish
    assert "build_push_argv" in publish
    assert "validate_push_argv" in publish
    assert "--no-follow-tags" in publish
    assert "Update-Run-Id" in publish
    assert "Update-Run-Attempt" in publish
    assert "force-with-lease" not in text
    assert "--force" not in text
    assert "classify_publication" in publish
    assert "product-identity-${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "name: product-identity\n" not in text


def test_update_rejects_non_main_and_classifies_from_origin() -> None:
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    assert "Reject non-main" in text
    assert "refs/heads/main" in text
    assert "git fetch --no-tags origin" in text
    assert 'PG_ESSAY_FEEDS_MAX_PAGE_FETCHES: "40"' in text
    assert 'PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS: "40"' in text


def test_attest_names_provenance_context() -> None:
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    publish = _job_block(text, "publish")
    assert "Write provenance context" in publish
    provenance = publish.split("Write provenance context", 1)[1]
    for key in ("source_sha", "candidate_digest", "product_sha", "subjects"):
        assert key in provenance
    for rel in _SEVEN:
        assert rel in provenance
    attest = publish.split("Attest published catalog and feeds", 1)[1]
    for rel in _SEVEN:
        assert rel in attest
    assert "product-provenance.json" in attest
    assert "retention-days: 90" in text
    assert "product-identity.json" in text
    assert "product-provenance.json" in text


def test_verify_product_trust_sequence() -> None:
    text = (_WORKFLOWS / "verify-product.yml").read_text(encoding="utf-8")
    assert "branches: [main]" in text
    assert "Event-only gate" in text
    assert "github.workflow_sha" in text
    assert "persist-credentials: false" in text
    assert "artifact-ids:" in text
    assert "name: product-identity\n" not in text
    assert "ref: main" not in text
    assert "ref: ${{ steps.identity.outputs.product_sha }}" in text
    assert "workflow_dispatch:" in text
    assert "product_sha:" in text
    assert "resolve_active_workflow_id" in text
    assert "fetch_exact_commit" in text
    # Event-only gate must appear before the first checkout/download.
    gate_at = text.index("Event-only gate")
    checkout_at = text.index("uses: actions/checkout@")
    download_at = text.index("uses: actions/download-artifact@")
    assert gate_at < checkout_at < download_at


def test_source_verify_skips_when_publish_gates_candidate() -> None:
    """Broken source product must not fail a candidate repair publish."""
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    verify = _job_block(text, "verify")
    header = verify.split("\n    steps:", 1)[0]
    assert "needs.update.outputs.action != 'updated'" in header
    assert "needs.update.outputs.action != 'state_changed'" in header
    publish = _job_block(text, "publish")
    assert "Gate publish on downloaded seven files" in publish


def test_privileged_update_jobs_do_not_force_uv_cache() -> None:
    publish = _job_block((_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8"), "publish")
    assert "enable-cache: true" not in publish
    assert "enable-cache: false" in publish

    verify_product = (_WORKFLOWS / "verify-product.yml").read_text(encoding="utf-8")
    assert "enable-cache: true" not in verify_product
    assert "enable-cache: false" in verify_product
