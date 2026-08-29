"""Unit tests for pydantic-settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.settings import Settings


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", raising=False)
    s = Settings()
    assert s.min_items >= 1
    assert s.timeout > 0
    assert s.validate_links is True
    assert s.enrich is True
    assert s.force is False
    assert "use_catalog_pipeline" not in Settings.model_fields


def test_settings_fields_have_descriptions() -> None:
    for name, field in Settings.model_fields.items():
        assert field.description, f"Settings.{name} missing description"


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_MIN_ITEMS", "10")
    monkeypatch.setenv("PG_ESSAY_FEEDS_REPO_ROOT", str(tmp_path))
    s = Settings()
    assert s.min_items == 10
    assert s.repo_root == tmp_path.resolve()


def test_enrich_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH", "false")
    assert Settings().enrich is False


def test_validate_links_env_false_opts_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", "false")
    assert Settings().validate_links is False


def test_validate_links_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", raising=False)
    assert Settings().validate_links is True


def test_link_workers_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """link_workers default 4; env override; reject out-of-range."""
    assert Settings().link_workers == 4
    monkeypatch.setenv("PG_ESSAY_FEEDS_LINK_WORKERS", "16")
    assert Settings().link_workers == 16
    monkeypatch.setenv("PG_ESSAY_FEEDS_LINK_WORKERS", "0")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("PG_ESSAY_FEEDS_LINK_WORKERS", "65")
    with pytest.raises(ValidationError):
        Settings()


def test_enrich_workers_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """enrich_workers default 4; env override; reject out-of-range."""
    assert Settings().enrich_workers == 4
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH_WORKERS", "16")
    assert Settings().enrich_workers == 16
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH_WORKERS", "0")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("PG_ESSAY_FEEDS_ENRICH_WORKERS", "65")
    with pytest.raises(ValidationError):
        Settings()


def test_host_cooldown_seconds_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUD-017: production default is a small nonzero per-host gap."""
    monkeypatch.delenv("PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS", raising=False)
    s = Settings()
    assert s.host_cooldown_seconds == 0.05
    field = Settings.model_fields["host_cooldown_seconds"]
    assert field.description == "Minimum seconds between requests to the same host."
    monkeypatch.setenv("PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS", "0")
    assert Settings().host_cooldown_seconds == 0.0
    monkeypatch.setenv("PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS", "-0.1")
    with pytest.raises(ValidationError):
        Settings()
