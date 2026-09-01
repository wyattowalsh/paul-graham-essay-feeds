"""PGF-2026-012: bind one candidate workspace to one product SHA.

Also locks setup-uv cache off on privileged publish / verify-product / release
jobs (audit item 19).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO / ".github" / "workflows"

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
    assert "Validate downloaded feeds" in publish
    assert "uv run pg-essay-feeds check" in publish


def test_publish_emits_product_sha_and_force_with_lease() -> None:
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    publish = _job_block(text, "publish")
    assert "product_sha=$(git rev-parse HEAD)" in publish
    assert '--force-with-lease="refs/heads/main:${expect}"' in publish
    assert "Re-check product tree" in publish


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


def test_verify_product_checks_product_sha_not_mutable_main() -> None:
    text = (_WORKFLOWS / "verify-product.yml").read_text(encoding="utf-8")
    assert "ref: main" not in text
    assert "ref: ${{ steps.identity.outputs.product_sha }}" in text
    assert "name: product-identity" in text
    assert "run-id: ${{ github.event.workflow_run.id }}" in text
    assert "workflow_dispatch:" in text
    assert "product_sha:" in text


def test_pages_checks_out_product_sha_on_update_feeds_workflow_run() -> None:
    text = (_WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
    assert "workflow_run:" in text
    assert 'workflows: ["Update feeds"]' in text
    assert "ref: ${{ steps.identity.outputs.product_sha }}" in text
    assert "ref: ${{ github.event.workflow_run.head_sha }}" not in text
    assert "name: product-identity" in text


def test_source_verify_skips_when_publish_gates_candidate() -> None:
    """Broken source product must not fail a candidate repair publish."""
    text = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
    verify = _job_block(text, "verify")
    header = verify.split("\n    steps:", 1)[0]
    assert "needs.update.outputs.action != 'updated'" in header
    assert "needs.update.outputs.action != 'state_changed'" in header
    publish = _job_block(text, "publish")
    assert "Gate publish on downloaded seven files" in publish


def test_privileged_jobs_do_not_force_uv_cache() -> None:
    publish = _job_block((_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8"), "publish")
    assert "enable-cache: true" not in publish
    assert "enable-cache: false" in publish

    verify_product = (_WORKFLOWS / "verify-product.yml").read_text(encoding="utf-8")
    assert "enable-cache: true" not in verify_product
    assert "enable-cache: false" in verify_product

    release = (_WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "enable-cache: true" not in release
    assert "enable-cache: false" in release

    ci = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "enable-cache: true" in ci
