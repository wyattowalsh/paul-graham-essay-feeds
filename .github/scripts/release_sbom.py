"""PGF-FINAL-003: generate and validate a default-runtime CycloneDX SBOM.

Uses locked ``uv export --format cyclonedx1.5`` for the default runtime graph
(no development group, no optional ``brotli`` extra), then normalizes
uncontrolled timestamp/serial values and validates against the official
CycloneDX JSON schema bundled by ``cyclonedx-python-lib``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from cyclonedx.schema import OutputFormat, SchemaVersion
from cyclonedx.validation import make_schemabased_validator

from paul_graham_essay_feeds import __version__

PROJECT_NAME: Final = "paul-graham-essay-feeds"
SPEC_VERSION: Final = "1.5"
DEV_PACKAGE_DENYLIST: Final = frozenset(
    {
        "pytest",
        "pytest-cov",
        "respx",
        "ruff",
        "ty",
        "cyclonedx-bom",
        "cyclonedx-python-lib",
    }
)
VERSION_OPERATOR_RE: Final = re.compile(r"==|>=|<=|~=|;")
PURL_RE: Final = re.compile(r"^pkg:pypi/[a-z0-9][a-z0-9._-]*@[^@\s]+$", re.IGNORECASE)
CHECKSUM_BASENAMES: Final = ("requirements.txt", "bom.cdx.json")


class ReleaseSbomError(RuntimeError):
    """Invalid SBOM or release metadata."""


def _run_uv(argv: list[str], *, cwd: Path) -> None:
    proc = subprocess.run(argv, cwd=cwd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip() or f"exit {proc.returncode}"
        raise ReleaseSbomError(f"uv {' '.join(argv[1:6])} failed: {detail}")


def export_requirements(repo_root: Path, dest: Path) -> None:
    """Write the frozen default-runtime requirements export (no project, no hashes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_uv(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-hashes",
            "--no-annotate",
            "--no-emit-project",
            "--no-header",
            "-o",
            str(dest),
        ],
        cwd=repo_root,
    )


def _export_uv_cyclonedx(repo_root: Path, dest: Path) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run_uv(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "cyclonedx1.5",
            "--preview-features",
            "sbom-export",
            "-o",
            str(dest),
        ],
        cwd=repo_root,
    )
    payload = json.loads(dest.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReleaseSbomError("uv CycloneDX export root must be an object")
    return payload


def _stable_serial(version: str, components: list[dict[str, Any]]) -> str:
    material = "|".join(
        f"{row.get('name', '')}@{row.get('version', '')}"
        for row in sorted(components, key=lambda item: str(item.get("bom-ref") or ""))
    )
    return f"urn:uuid:{uuid5(NAMESPACE_URL, f'{PROJECT_NAME}:{version}:{material}')}"


def normalize_runtime_bom(payload: dict[str, Any], *, version: str) -> dict[str, Any]:
    """Make uv's CycloneDX export version-adaptive and reproducible."""
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ReleaseSbomError("SBOM metadata must be an object")
    metadata.pop("timestamp", None)
    component = metadata.setdefault("component", {})
    if not isinstance(component, dict):
        raise ReleaseSbomError("SBOM root component must be an object")
    component["type"] = "library"
    component["name"] = PROJECT_NAME
    component["version"] = version
    component["purl"] = f"pkg:pypi/{PROJECT_NAME}@{version}"
    component.setdefault("bom-ref", f"{PROJECT_NAME}@{version}")
    components = payload.get("components")
    if not isinstance(components, list):
        raise ReleaseSbomError("SBOM components must be a list")
    for component in components:
        if not isinstance(component, dict):
            raise ReleaseSbomError("component must be an object")
        version_text = component.get("version")
        name = component.get("name")
        if isinstance(version_text, str) and VERSION_OPERATOR_RE.search(version_text):
            raise ReleaseSbomError(f"component {name} version is not a bare version (PEP 508)")
    payload["bomFormat"] = "CycloneDX"
    payload["specVersion"] = SPEC_VERSION
    payload["version"] = 1
    payload["serialNumber"] = _stable_serial(version, components)
    return json.loads(json.dumps(payload, sort_keys=True))


def validate_runtime_bom(
    payload: dict[str, Any],
    *,
    version: str,
    requirements_text: str,
    workspace: Path | None = None,
) -> None:
    """Fail closed if the BOM is not a schema-valid default-runtime document."""
    raw = json.dumps(payload)
    validator = make_schemabased_validator(OutputFormat.JSON, SchemaVersion.V1_5)
    errors = validator.validate_str(raw)
    if errors:
        raise ReleaseSbomError(f"CycloneDX schema validation failed: {errors}")
    if payload.get("specVersion") != SPEC_VERSION:
        raise ReleaseSbomError(f"specVersion must be {SPEC_VERSION}")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ReleaseSbomError("metadata missing")
    if "timestamp" in metadata:
        raise ReleaseSbomError("reproducible SBOM must not include metadata.timestamp")
    root = metadata.get("component")
    if not isinstance(root, dict):
        raise ReleaseSbomError("root component missing")
    if root.get("name") != PROJECT_NAME or root.get("version") != version:
        raise ReleaseSbomError("root component name/version must match package metadata")
    components = payload.get("components")
    if not isinstance(components, list):
        raise ReleaseSbomError("components missing")
    names: list[str] = []
    refs: list[str] = []
    runtime_names = _requirement_names(requirements_text)
    for component in components:
        if not isinstance(component, dict):
            raise ReleaseSbomError("component must be an object")
        name = component.get("name")
        version_text = component.get("version")
        bom_ref = component.get("bom-ref")
        purl = component.get("purl")
        if not isinstance(name, str) or not name:
            raise ReleaseSbomError("component name missing")
        if not isinstance(version_text, str) or not version_text:
            raise ReleaseSbomError(f"component {name} missing version")
        if VERSION_OPERATOR_RE.search(version_text):
            raise ReleaseSbomError(f"component {name} version is not a bare version")
        if name.lower() in DEV_PACKAGE_DENYLIST or name.lower() == "brotli":
            raise ReleaseSbomError(f"component {name} is not a default runtime dependency")
        if not isinstance(bom_ref, str) or not bom_ref:
            raise ReleaseSbomError(f"component {name} missing bom-ref")
        if not isinstance(purl, str) or not PURL_RE.match(purl):
            raise ReleaseSbomError(f"component {name} has invalid purl {purl!r}")
        names.append(name)
        refs.append(bom_ref)
    if len(names) != len(set(names)):
        raise ReleaseSbomError("component names must be unique")
    if len(refs) != len(set(refs)):
        raise ReleaseSbomError("bom-ref values must be unique")
    if set(names) != runtime_names:
        raise ReleaseSbomError(
            "SBOM components must match the frozen default runtime export: "
            f"extra={sorted(set(names) - runtime_names)} "
            f"missing={sorted(runtime_names - set(names))}"
        )
    blob = json.dumps(payload)
    if workspace is not None:
        resolved = str(workspace.resolve())
        if resolved in blob:
            raise ReleaseSbomError("SBOM must not contain the absolute checkout path")


def _requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        name, _, _rest = line.partition("==")
        names.add(name.strip().lower())
    return names


def write_bom(repo_root: Path, dest: Path, *, version: str | None = None) -> dict[str, Any]:
    """Generate, normalize, and schema-validate ``bom.cdx.json``."""
    resolved_version = version or __version__
    requirements_path = dest.parent / "requirements.txt"
    if not requirements_path.is_file():
        raise ReleaseSbomError("requirements.txt must be exported before the SBOM")
    payload = normalize_runtime_bom(_export_uv_cyclonedx(repo_root, dest), version=resolved_version)
    requirements = requirements_path.read_text(encoding="utf-8")
    validate_runtime_bom(
        payload,
        version=resolved_version,
        requirements_text=requirements,
        workspace=repo_root,
    )
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def checksum_paths(dist: Path) -> list[Path]:
    """Release assets that must appear in ``SHA256SUMS.txt`` exactly once."""
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    extras = [dist / name for name in CHECKSUM_BASENAMES]
    missing = [path for path in extras if not path.is_file()]
    if not wheels or not sdists or missing:
        raise ReleaseSbomError("checksums require wheel, sdist, requirements.txt, and bom.cdx.json")
    return wheels + sdists + extras


def write_sha256sums(dist: Path) -> Path:
    """Write ``SHA256SUMS.txt`` after all non-checksum release assets exist."""
    lines: list[str] = []
    for path in checksum_paths(dist):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    names = [path.name for path in checksum_paths(dist)]
    if len(names) != len(set(names)):
        raise ReleaseSbomError("release asset filenames must be unique")
    dest = dist / "SHA256SUMS.txt"
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def verify_sha256sums(dist: Path) -> None:
    """Every listed checksum verifies; the manifest names expected assets once."""
    manifest = dist / "SHA256SUMS.txt"
    if not manifest.is_file():
        raise ReleaseSbomError("SHA256SUMS.txt is missing")
    expected = {path.name for path in checksum_paths(dist)}
    seen: list[str] = []
    for raw in manifest.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        digest, name = line.split(None, 1)
        name = name.lstrip("*").strip()
        seen.append(name)
        path = dist / name
        if not path.is_file():
            raise ReleaseSbomError(f"checksum names missing file {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise ReleaseSbomError(f"checksum mismatch for {name}")
    if len(seen) != len(set(seen)):
        raise ReleaseSbomError("SHA256SUMS.txt names an asset more than once")
    if set(seen) != expected:
        raise ReleaseSbomError(
            f"SHA256SUMS.txt coverage mismatch extra={sorted(set(seen) - expected)} "
            f"missing={sorted(expected - set(seen))}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate runtime CycloneDX SBOM")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--checksums", action="store_true")
    args = parser.parse_args(argv)
    dist = args.dist if args.dist.is_absolute() else (Path.cwd() / args.dist)
    export_requirements(args.repo_root, dist / "requirements.txt")
    write_bom(args.repo_root, dist / "bom.cdx.json")
    if args.checksums:
        write_sha256sums(dist)
        verify_sha256sums(dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
