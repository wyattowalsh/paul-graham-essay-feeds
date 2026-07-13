"""Typed configuration loading from defaults, TOML, environment, and CLI."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from paul_graham_essay_feeds import __version__
from paul_graham_essay_feeds.domain import (
    DEFAULT_CATEGORY,
    DEFAULT_FEED_ID,
    MAX_SOURCE_BYTES,
    MIN_BASELINE_ITEMS,
    SOURCE_URL,
    FeedError,
    PublicUrls,
    canonicalize_public_url,
)

__all__ = ["AppConfig", "load_config"]


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Resolved application configuration.

    Attributes
    ----------
    repo_root :
        Absolute repository / working root used to resolve relative paths.
    source_url :
        Official essays index URL.
    allowed_hosts :
        Host allowlist for item URLs (essays + Turbify chapters).
    source_allowed_hosts :
        Hosts permitted for the index URL after HTTP redirects.
    min_items :
        Minimum item safety floor (initialized from baseline 233).
    max_response_bytes :
        Hard cap on source response size.
    retries :
        Number of retries after the first attempt for transient HTTP failures.
    timeout :
        Socket timeout in seconds for HTTP fetches.
    feed_title :
        Syndication title.
    feed_description :
        Syndication description.
    author_name :
        Author name for feed metadata.
    author_url :
        Author homepage URL.
    language :
        Language tag for JSON Feed (``en``); RSS uses ``en-US`` in renderer.
    home_page_url :
        Official index / alternate home URL.
    public_base_url :
        Deployed site base URL, or ``None`` when not configured.
    feed_id :
        Stable Atom feed identifier (tag URI).
    category :
        Category label (``Essays``).
    generator :
        Generator string including package version.
    path_rss, path_atom, path_json_feed, path_opml :
        Output paths for feed formats.
    path_essays, path_state, path_validation, path_checksums :
        Persistence and report paths.
    allow_removals :
        Policy default for accepting removals.
    allow_nonprefix_additions :
        Policy default for mid-history insertions.
    backup_count :
        Number of rotated backups to retain (best-effort).
    """

    repo_root: Path
    source_url: str
    allowed_hosts: frozenset[str]
    source_allowed_hosts: frozenset[str]
    min_items: int
    max_response_bytes: int
    retries: int
    timeout: float
    feed_title: str
    feed_description: str
    author_name: str
    author_url: str
    language: str
    home_page_url: str
    public_base_url: str | None
    feed_id: str
    category: str
    generator: str
    path_rss: Path
    path_atom: Path
    path_json_feed: Path
    path_opml: Path
    path_essays: Path
    path_state: Path
    path_validation: Path
    path_checksums: Path
    allow_removals: bool
    allow_nonprefix_additions: bool
    backup_count: int

    def public_urls(self) -> PublicUrls | None:
        """Derive public feed URLs when a base URL is configured."""
        if not self.public_base_url:
            return None
        return PublicUrls.from_base(self.public_base_url)

    def require_public_urls(self) -> PublicUrls:
        """Return public URLs or raise when OPML / full multi-format build needs them."""
        public = self.public_urls()
        if public is None:
            raise FeedError(
                "A public base URL is required to build the OPML catalog and "
                "self/feed links. Set deployment.public_base_url, "
                "PG_ESSAY_FEEDS_PUBLIC_BASE_URL, or --public-base-url."
            )
        return public


def _default_root() -> Path:
    """Detect package repo root (directory containing pyproject.toml)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise FeedError(f"Config root must be a table: {path}")
    return data


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a TOML table section as a plain dict."""
    value = data.get(name)
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def load_config(
    *,
    repo_root: Path | None = None,
    config_path: Path | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Load configuration with merge order CLI > env > file > defaults.

    Parameters
    ----------
    repo_root :
        Working root; default discovers the project containing this package.
    config_path :
        Optional TOML path. Defaults to ``config.toml`` then
        ``config.example.toml`` under the root.
    cli_overrides :
        Mapping of already-parsed CLI values (only non-``None`` keys applied).
    """
    root = (repo_root or _default_root()).resolve()
    file_data: dict[str, Any] = {}
    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(_resolve_path(root, config_path))
    else:
        env_cfg = os.environ.get("PG_ESSAY_FEEDS_CONFIG")
        if env_cfg:
            candidates.append(_resolve_path(root, env_cfg))
        candidates.extend([root / "config.toml", root / "config.example.toml"])
    for candidate in candidates:
        if candidate.is_file():
            file_data = _read_toml(candidate)
            break

    source = _section(file_data, "source")
    feed = _section(file_data, "feed")
    deployment = _section(file_data, "deployment")
    outputs = _section(file_data, "outputs")
    policy = _section(file_data, "policy")

    hosts = source.get("allowed_hosts") or ["paulgraham.com", "sep.turbifycdn.com"]
    allowed = frozenset(str(h) for h in hosts)
    source_hosts_raw = source.get("source_allowed_hosts") or ["paulgraham.com"]
    source_allowed = frozenset(str(h) for h in source_hosts_raw)

    public = deployment.get("public_base_url")
    if isinstance(public, str) and public.strip():
        public_base: str | None = canonicalize_public_url(public.strip(), field="public_base_url")
    else:
        public_base = None

    env_public = os.environ.get("PG_ESSAY_FEEDS_PUBLIC_BASE_URL")
    if env_public and env_public.strip():
        public_base = canonicalize_public_url(
            env_public.strip(), field="PG_ESSAY_FEEDS_PUBLIC_BASE_URL"
        )

    min_items = int(source.get("minimum_items", MIN_BASELINE_ITEMS))
    env_min = os.environ.get("PG_ESSAY_FEEDS_MIN_ITEMS")
    if env_min:
        min_items = int(env_min)

    cfg = AppConfig(
        repo_root=root,
        source_url=str(source.get("url", SOURCE_URL)),
        allowed_hosts=allowed,
        source_allowed_hosts=source_allowed,
        min_items=min_items,
        max_response_bytes=int(source.get("max_response_bytes", MAX_SOURCE_BYTES)),
        retries=int(source.get("retries", 3)),
        timeout=float(source.get("timeout", 30.0)),
        feed_title=str(feed.get("title", "Paul Graham: Essays")),
        feed_description=str(
            feed.get(
                "description",
                "Unofficial metadata feeds for Paul Graham's essays, ordered "
                "newest to oldest from the official index.",
            )
        ),
        author_name=str(feed.get("author_name", "Paul Graham")),
        author_url=str(feed.get("author_url", "https://paulgraham.com/")),
        language=str(feed.get("language", "en")),
        home_page_url=str(feed.get("home_page_url", SOURCE_URL)),
        public_base_url=public_base,
        feed_id=str(feed.get("feed_id", DEFAULT_FEED_ID)),
        category=str(feed.get("category", DEFAULT_CATEGORY)),
        generator=f"pg-essay-feeds/{__version__}",
        path_rss=_resolve_path(root, str(outputs.get("rss", "feeds/rss.xml"))),
        path_atom=_resolve_path(root, str(outputs.get("atom", "feeds/atom.xml"))),
        path_json_feed=_resolve_path(root, str(outputs.get("json_feed", "feeds/feed.json"))),
        path_opml=_resolve_path(root, str(outputs.get("opml", "feeds/subscriptions.opml"))),
        path_essays=_resolve_path(root, str(outputs.get("items", "data/essays.json"))),
        path_state=_resolve_path(root, str(outputs.get("state", "data/state.json"))),
        path_validation=_resolve_path(
            root, str(outputs.get("validation", "reports/validation.json"))
        ),
        path_checksums=_resolve_path(root, str(outputs.get("checksums", "SHA256SUMS"))),
        allow_removals=bool(policy.get("allow_removals", False)),
        allow_nonprefix_additions=bool(policy.get("allow_nonprefix_additions", False)),
        backup_count=int(policy.get("backup_count", 3) or 3),
    )

    overrides = {k: v for k, v in (cli_overrides or {}).items() if v is not None}
    if not overrides:
        return cfg

    mapping: dict[str, Any] = {}
    if "source_url" in overrides:
        mapping["source_url"] = str(overrides["source_url"])
    if "min_items" in overrides:
        mapping["min_items"] = int(overrides["min_items"])
    if "timeout" in overrides:
        mapping["timeout"] = float(overrides["timeout"])
    if "retries" in overrides:
        mapping["retries"] = int(overrides["retries"])
    if "public_base_url" in overrides:
        value = overrides["public_base_url"]
        if value == "" or value is False:
            mapping["public_base_url"] = None
        else:
            mapping["public_base_url"] = canonicalize_public_url(
                str(value), field="--public-base-url"
            )
    if "allow_removals" in overrides:
        mapping["allow_removals"] = bool(overrides["allow_removals"])
    if "allow_nonprefix_additions" in overrides:
        mapping["allow_nonprefix_additions"] = bool(overrides["allow_nonprefix_additions"])
    if "repo_root" in overrides:
        mapping["repo_root"] = Path(overrides["repo_root"]).resolve()
    return replace(cfg, **mapping)
