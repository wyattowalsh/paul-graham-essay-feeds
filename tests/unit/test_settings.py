"""Unit tests for pydantic-settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds.settings import (
    DEFAULT_MAX_LINK_VALIDATIONS,
    DEFAULT_MAX_PAGE_FETCHES,
    Settings,
    budget_label,
)


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PG_ESSAY_FEEDS_VALIDATE_LINKS", raising=False)
    monkeypatch.delenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", raising=False)
    monkeypatch.delenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", raising=False)
    monkeypatch.delenv("PG_ESSAY_FEEDS_ALL_PAGES", raising=False)
    s = Settings()
    assert s.min_items >= 1
    assert s.timeout > 0
    assert s.validate_links is True
    assert s.enrich is True
    assert s.force is False
    assert s.max_page_fetches == DEFAULT_MAX_PAGE_FETCHES == 40
    assert s.max_link_validations == DEFAULT_MAX_LINK_VALIDATIONS == 40
    assert s.all_pages is False
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


def test_validate_links_description_mentions_skip_network() -> None:
    text = Settings.model_fields["validate_links"].description or ""
    assert "no-op" in text
    assert "skip-network" in text


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
    assert s.host_cooldown_seconds == 0.25
    field = Settings.model_fields["host_cooldown_seconds"]
    assert field.description == "Minimum seconds between requests to the same host."
    monkeypatch.setenv("PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS", "0")
    assert Settings().host_cooldown_seconds == 0.0
    monkeypatch.setenv("PG_ESSAY_FEEDS_HOST_COOLDOWN_SECONDS", "-0.1")
    with pytest.raises(ValidationError):
        Settings()


def test_fetch_budget_defaults_match_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """PGF-2026-014: unbounded None is no longer the default."""
    monkeypatch.delenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", raising=False)
    monkeypatch.delenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", raising=False)
    monkeypatch.delenv("PG_ESSAY_FEEDS_ALL_PAGES", raising=False)
    s = Settings()
    assert s.max_page_fetches == 40
    assert s.max_link_validations == 40
    assert budget_label(s.max_page_fetches) == "40"
    assert budget_label(None) == "unlimited"


def test_fetch_budget_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", "7")
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", "3")
    s = Settings()
    assert s.max_page_fetches == 7
    assert s.max_link_validations == 3


def test_fetch_budget_env_unlimited_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", "none")
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", "unlimited")
    s = Settings()
    assert s.max_page_fetches is None
    assert s.max_link_validations is None


def test_fetch_budget_empty_env_keeps_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank env must not uncap (PGF-2026-014)."""
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", "")
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", "  ")
    s = Settings()
    assert s.max_page_fetches == 40
    assert s.max_link_validations == 40


def test_all_pages_env_uncaps_both_budgets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", "40")
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_LINK_VALIDATIONS", "40")
    monkeypatch.setenv("PG_ESSAY_FEEDS_ALL_PAGES", "true")
    s = Settings()
    assert s.all_pages is True
    assert s.max_page_fetches is None
    assert s.max_link_validations is None


def test_all_pages_constructor_uncaps() -> None:
    s = Settings.model_validate({"all_pages": True, "max_page_fetches": 40})
    assert s.max_page_fetches is None
    assert s.max_link_validations is None


def test_fetch_budget_rejects_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PG_ESSAY_FEEDS_MAX_PAGE_FETCHES", "-1")
    with pytest.raises(ValidationError):
        Settings()
