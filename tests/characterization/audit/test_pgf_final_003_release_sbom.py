"""PGF-FINAL-003: release SBOM is a real CycloneDX graph, not a fake JSON object."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cyclonedx.schema import OutputFormat, SchemaVersion
from cyclonedx.validation import make_schemabased_validator

_REPO = Path(__file__).resolve().parents[3]
_RELEASE = _REPO / ".github" / "workflows" / "release.yml"
_HELPER = _REPO / ".github" / "scripts" / "release_sbom.py"
_spec = importlib.util.spec_from_file_location("release_sbom_under_test", _HELPER)
if _spec is None or _spec.loader is None:
    msg = f"unable to load release SBOM helper from {_HELPER}"
    raise RuntimeError(msg)
_sbom = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _sbom
_spec.loader.exec_module(_sbom)
ReleaseSbomError = _sbom.ReleaseSbomError
export_requirements = _sbom.export_requirements
normalize_runtime_bom = _sbom.normalize_runtime_bom
validate_runtime_bom = _sbom.validate_runtime_bom
verify_sha256sums = _sbom.verify_sha256sums
write_bom = _sbom.write_bom
write_sha256sums = _sbom.write_sha256sums


def _release_text() -> str:
    return _RELEASE.read_text(encoding="utf-8")


def _sample_bom(*, version: str = "0.28.1") -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:11111111-1111-4111-8111-111111111111",
        "version": 1,
        "metadata": {
            "timestamp": "2024-01-01T00:00:00Z",
            "tools": [{"name": "uv", "version": "0.12.0"}],
            "component": {"type": "library", "name": "pkg", "version": "0.0.0"},
        },
        "components": [
            {
                "type": "library",
                "name": "httpx",
                "version": version,
                "purl": f"pkg:pypi/httpx@{version}",
                "bom-ref": f"httpx@{version}",
            }
        ],
        "dependencies": [{"ref": f"httpx@{version}"}],
    }


def _dist_with_assets(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "paul_graham_essay_feeds-1.0.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "paul_graham_essay_feeds-1.0.0.tar.gz").write_bytes(b"sdist")
    (dist / "requirements.txt").write_text("httpx==0.28.1\n", encoding="utf-8")
    (dist / "bom.cdx.json").write_text("{}\n", encoding="utf-8")
    return dist


def test_release_workflow_invokes_sbom_script_not_inline_python() -> None:
    text = _release_text()
    assert "release_sbom.py" in text
    assert '"bomFormat": "CycloneDX"' not in text
    assert "version = next(p for p in Path" not in text
    assert "bom = {" not in text


def test_release_checksums_and_attestations_cover_sbom_and_requirements() -> None:
    text = _release_text()
    assert "sha256sum -- *.whl *.tar.gz requirements.txt bom.cdx.json" in text
    assert "dist/requirements.txt" in text
    assert "dist/bom.cdx.json" in text
    assert "dist/SHA256SUMS.txt" in text
    assert "dist/*.whl" in text
    assert "dist/*.tar.gz" in text


def test_release_build_syncs_locked_dev_group() -> None:
    text = _release_text()
    assert "uv sync --locked --all-groups" in text
    pyproject = (_REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "cyclonedx-bom" in pyproject


def test_normalize_runtime_bom_is_deterministic() -> None:
    first = normalize_runtime_bom(_sample_bom(), version="1.0.0")
    second = normalize_runtime_bom(_sample_bom(), version="1.0.0")
    assert first == second
    assert "timestamp" not in first["metadata"]
    assert first["serialNumber"].startswith("urn:uuid:")
    assert first["metadata"]["component"]["version"] == "1.0.0"
    assert first["metadata"]["component"]["purl"] == ("pkg:pypi/paul-graham-essay-feeds@1.0.0")


def test_normalize_runtime_bom_rejects_marker_versions() -> None:
    with pytest.raises(ReleaseSbomError, match="PEP 508"):
        normalize_runtime_bom(
            _sample_bom(version="1.2.3 ; python_version >= '3.12'"),
            version="1.0.0",
        )


def test_validate_runtime_bom_rejects_wrong_root_version(tmp_path: Path) -> None:
    dest = tmp_path / "bom.cdx.json"
    requirements = tmp_path / "requirements.txt"
    export_requirements(_REPO, requirements)
    payload = write_bom(_REPO, dest, version="1.0.0")
    payload["metadata"]["component"]["version"] = "9.9.9"
    with pytest.raises(ReleaseSbomError, match="name/version"):
        validate_runtime_bom(
            payload,
            version="1.0.0",
            requirements_text=requirements.read_text(encoding="utf-8"),
        )


def test_validate_runtime_bom_rejects_duplicate_bom_refs(tmp_path: Path) -> None:
    dest = tmp_path / "bom.cdx.json"
    requirements = tmp_path / "requirements.txt"
    export_requirements(_REPO, requirements)
    payload = write_bom(_REPO, dest, version="1.0.0")
    payload["components"][1]["bom-ref"] = payload["components"][0]["bom-ref"]
    with pytest.raises(ReleaseSbomError):
        validate_runtime_bom(
            payload,
            version="1.0.0",
            requirements_text=requirements.read_text(encoding="utf-8"),
        )


def test_write_bom_matches_lockfile_graph(tmp_path: Path) -> None:
    requirements_path = tmp_path / "requirements.txt"
    dest = tmp_path / "bom.cdx.json"
    export_requirements(_REPO, requirements_path)
    first = write_bom(_REPO, dest, version="1.0.0")
    second = write_bom(_REPO, dest, version="1.0.0")
    assert first == second
    payload = dest.read_text(encoding="utf-8")
    errors = make_schemabased_validator(OutputFormat.JSON, SchemaVersion.V1_5).validate_str(payload)
    assert not errors
    independent = json.loads(payload)
    assert independent["specVersion"] == "1.5"
    assert independent["metadata"]["component"]["version"] == "1.0.0"
    versions = [str(item.get("version", "")) for item in independent["components"]]
    assert all("==" not in version and ";" not in version for version in versions)
    names = {str(item.get("name", "")).lower() for item in independent["components"]}
    assert "brotli" not in names
    assert "ruff" not in names
    assert "pytest" not in names
    assert "httpx" in names
    assert "pydantic" in names
    assert "selectolax" in names
    assert str(_REPO.resolve()) not in payload


def test_checksum_manifest_covers_expected_assets_once(tmp_path: Path) -> None:
    dist = _dist_with_assets(tmp_path)
    write_sha256sums(dist)
    verify_sha256sums(dist)
    lines = [
        line
        for line in (dist / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    names = [line.split(maxsplit=1)[1].strip() for line in lines]
    assert names.count("bom.cdx.json") == 1
    assert names.count("requirements.txt") == 1
    assert any(name.endswith(".whl") for name in names)
    assert any(name.endswith(".tar.gz") for name in names)
    expected = hashlib.sha256((dist / "bom.cdx.json").read_bytes()).hexdigest()
    assert any(line.startswith(expected) for line in lines)


def test_checksum_verification_fails_on_modified_asset(tmp_path: Path) -> None:
    dist = _dist_with_assets(tmp_path)
    write_sha256sums(dist)
    (dist / "bom.cdx.json").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ReleaseSbomError, match="checksum mismatch"):
        verify_sha256sums(dist)


def test_checksum_verification_fails_when_asset_missing(tmp_path: Path) -> None:
    dist = _dist_with_assets(tmp_path)
    write_sha256sums(dist)
    (dist / "bom.cdx.json").unlink()
    with pytest.raises(ReleaseSbomError, match="checksums require"):
        verify_sha256sums(dist)


def test_uv_cyclonedx_export_is_available() -> None:
    proc = subprocess.run(
        ["uv", "export", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "cyclonedx" in proc.stdout.lower()
