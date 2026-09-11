"""WF-COHERENCE: cross-workflow artifact, route, and helper agreement."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_WORKFLOWS = _REPO / ".github" / "workflows"
_HELPER = ".github/scripts/product_identity.py"

_UPDATE = (_WORKFLOWS / "update-feeds.yml").read_text(encoding="utf-8")
_VERIFY = (_WORKFLOWS / "verify-product.yml").read_text(encoding="utf-8")
_CI = (_WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
_PAGES = (_WORKFLOWS / "pages.yml").read_text(encoding="utf-8")
_RELEASE = (_WORKFLOWS / "release.yml").read_text(encoding="utf-8")


def test_consumers_load_helper_from_workflow_sha_checkout() -> None:
    for text in (_VERIFY, _CI, _PAGES):
        assert "ref: ${{ github.workflow_sha }}" in text
        assert _HELPER in text
        assert text.index("github.workflow_sha") < text.index(_HELPER)


def test_producer_and_consumers_share_scoped_identity_artifact_name() -> None:
    scoped = "product-identity-${{ github.run_id }}-${{ github.run_attempt }}"
    assert scoped in _UPDATE
    assert "name: product-identity\n" not in _UPDATE
    assert "name: product-identity\n" not in _VERIFY
    assert "name: product-identity\n" not in _CI
    assert "name: product-identity\n" not in _PAGES
    assert "artifact-ids:" in _VERIFY
    assert "artifact-ids:" in _CI
    assert "artifact-ids:" in _PAGES


def test_ci_authorization_and_noop_names_are_run_scoped() -> None:
    assert "ci-authorization-" in _CI
    assert "ci-authorization-" in _PAGES
    assert "ci-noop-${{ github.run_id }}-${{ github.run_attempt }}.json" in _CI
    assert "build_ci_authorization_document" in _CI
    assert "expected_ci_authorization_artifact_name" in _PAGES


def test_pages_update_is_observer_ci_is_deployer() -> None:
    observe = _PAGES.split("observe_update:", 1)[1].split("\n  reconcile:", 1)[0]
    assert "pages: write" not in observe
    assert "id-token: write" not in observe
    assert "actions/deploy-pages@" not in observe
    assert "actions/upload-pages-artifact@" not in observe
    assert "no-deploy" in observe
    assert "pages-current-main-v1" in _PAGES
    assert "actions/deploy-pages@" in _PAGES.split("reconcile:", 1)[1]
    assert "workflow_dispatch:" not in _PAGES
    assert "push:" not in _PAGES


def test_update_never_force_pushes_and_release_is_not_identity_consumer() -> None:
    assert "force-with-lease" not in _UPDATE
    assert "--force" not in _UPDATE
    assert "build_push_argv" in _UPDATE
    assert "validate_push_argv" in _UPDATE
    assert _HELPER not in _RELEASE
    assert "product-identity-" not in _RELEASE


def test_cross_run_downloads_bind_source_run_id() -> None:
    for text in (_VERIFY, _CI, _PAGES):
        assert "run-id: ${{ github.event.workflow_run.id }}" in text


def test_update_binds_generation_to_event_sha() -> None:
    """PGF-FINAL-002: code, lockfile, and catalog come from one immutable SHA."""
    assert "ref: ${{ github.sha }}" in _UPDATE
    assert "Prove HEAD is the event revision" in _UPDATE
    assert "HEAD ${head} != GITHUB_SHA" in _UPDATE
    assert "source_sha=${head}" in _UPDATE
    assert 'git checkout "${source_sha}" -- catalog.json feeds' not in _UPDATE
    assert "git checkout FETCH_HEAD -- catalog.json" not in _UPDATE
    assert "git checkout origin/main -- catalog.json" not in _UPDATE
    assert 'git checkout "${product_sha}" -- catalog.json feeds' in _UPDATE
    for job_label in ("\n  update:", "\n  verify:", "\n  publish:"):
        assert job_label in _UPDATE
        start = _UPDATE.index(job_label)
        chunk = _UPDATE[start : start + 2500]
        assert "ref: ${{ github.sha }}" in chunk
        assert "Prove HEAD is the event revision" in chunk


def test_helper_importlib_loaders_register_sys_modules() -> None:
    """Dataclass annotation resolution requires the module in sys.modules."""
    needle = '__import__("sys").modules[spec.name] = pi'
    for text in (_UPDATE, _VERIFY, _CI, _PAGES):
        assert needle in text
        assert text.count("spec.loader.exec_module(pi)") == text.count(needle)
