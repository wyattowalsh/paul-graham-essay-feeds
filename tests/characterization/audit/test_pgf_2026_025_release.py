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


def test_release_requires_tag_is_ancestor_of_main() -> None:
    """PGF-2026-033: tagged SHA must be on origin/main; no depth-1 ancestry fetch."""
    text = _RELEASE.read_text(encoding="utf-8")
    assert "git fetch origin main --filter=blob:none" in text
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in text
    assert "git fetch origin main --depth=1" not in text


def test_release_mirrors_ci_feed_contracts_smoke_and_py_typed() -> None:
    """PGF-2026-034: release quality/build includes the CI-only product checks."""
    text = _RELEASE.read_text(encoding="utf-8")
    assert "Assert feed format contracts" in text
    assert "Offline pipeline smoke" in text
    assert "paul_graham_essay_feeds/py.typed" in text
    ci = _CI.read_text(encoding="utf-8")
    assert "Assert feed format contracts" in ci
    assert "Offline pipeline smoke" in ci
    assert "--extra brotli" in ci
    assert 'python-version: "3.13"' in text
    assert "uv run pytest --cov-fail-under=0" in ci


def test_sdist_excludes_maintainer_grok_tree() -> None:
    """PGF-2026-039: Hatch sdist must not ship tracked .grok automation."""
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"/.grok"' in pyproject or "'/.grok'" in pyproject
