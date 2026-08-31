"""PGF-2026-025: release.yml uses the same raw coverage floor as CI."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_RELEASE = _REPO / ".github" / "workflows" / "release.yml"
_CI = _REPO / ".github" / "workflows" / "ci.yml"


def test_release_quality_runs_raw_coverage_xml_helper() -> None:
    text = _RELEASE.read_text(encoding="utf-8")
    assert "--cov-report=xml:coverage.xml" in text
    assert "uv run python -m tests.coverage_xml coverage.xml" in text
    ci = _CI.read_text(encoding="utf-8")
    assert "uv run python -m tests.coverage_xml coverage.xml" in ci


def test_release_attests_dist_and_writes_checksums() -> None:
    text = _RELEASE.read_text(encoding="utf-8")
    assert "attest-build-provenance" in text
    assert "SHA256SUMS.txt" in text
    assert "id-token: write" in text
    assert "attestations: write" in text
    assert "subject-path:" in text
