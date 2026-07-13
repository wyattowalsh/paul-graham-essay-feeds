"""Orchestration for update, build, check, and diff (no CLI parsing)."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from paul_graham_essay_feeds.config import AppConfig
from paul_graham_essay_feeds.domain import (
    BuildContext,
    ChangeSet,
    EssayItem,
    FeedError,
    logical_signature_sha256,
    sha256_bytes,
    utc_now,
)
from paul_graham_essay_feeds.extract import extract_items, extraction_meta_dict
from paul_graham_essay_feeds.fetch import decode_source, fetch_source
from paul_graham_essay_feeds.io import (
    acquire_lock,
    publish_artifacts,
)
from paul_graham_essay_feeds.reconcile import reconcile_items
from paul_graham_essay_feeds.renderers import (
    render_atom,
    render_json_feed,
    render_opml,
    render_rss,
)
from paul_graham_essay_feeds.state import (
    default_state_payload,
    essays_bytes,
    load_essays,
    load_optional_json,
    load_state,
    merge_items,
)
from paul_graham_essay_feeds.validation import (
    build_validation_report,
    validate_all_formats,
)

__all__ = ["run_build", "run_check", "run_diff", "run_update"]


def _baseline_path(cfg: AppConfig) -> Path:
    return cfg.repo_root / "data" / "baseline-items.json"


def _lock_path(cfg: AppConfig) -> Path:
    return cfg.repo_root / "data" / ".update.lock"


def _make_context(
    cfg: AppConfig,
    items: tuple[EssayItem, ...],
    *,
    build_updated_at=None,
) -> BuildContext:
    return BuildContext(
        items=items,
        feed_title=cfg.feed_title,
        feed_description=cfg.feed_description,
        author_name=cfg.author_name,
        author_url=cfg.author_url,
        language=cfg.language,
        home_page_url=cfg.home_page_url,
        public=cfg.public_urls(),
        feed_id=cfg.feed_id,
        generator=cfg.generator,
        build_updated_at=build_updated_at or utc_now(),
        category=cfg.category,
    )


def _signature(cfg: AppConfig, items: tuple[EssayItem, ...]) -> str:
    return logical_signature_sha256(
        items,
        public_base_url=cfg.public_base_url,
        feed_title=cfg.feed_title,
        feed_description=cfg.feed_description,
        generator=cfg.generator,
    )


def _render_all(ctx: BuildContext) -> dict[str, bytes]:
    public = ctx.public
    if public is None:
        raise FeedError(
            "Full multi-format build requires a public base URL for OPML and self links."
        )
    return {
        "rss": render_rss(ctx),
        "atom": render_atom(ctx),
        "json_feed": render_json_feed(ctx),
        "opml": render_opml(ctx),
    }


def _paths(cfg: AppConfig) -> dict[str, Path]:
    return {
        "rss": cfg.path_rss,
        "atom": cfg.path_atom,
        "json_feed": cfg.path_json_feed,
        "opml": cfg.path_opml,
    }


def _format_hash_map(paths: dict[str, Path], blobs: dict[str, bytes]) -> dict[str, Any]:
    return {
        name: {
            "path": str(paths[name]),
            "sha256": sha256_bytes(blobs[name]),
            "ok": True,
        }
        for name in blobs
    }


def _checksum_targets(cfg: AppConfig) -> list[Path]:
    return [
        cfg.path_rss,
        cfg.path_atom,
        cfg.path_json_feed,
        cfg.path_opml,
        cfg.path_essays,
        cfg.path_state,
        cfg.path_validation,
    ]


def _json_bytes(value: dict[str, Any] | list[Any]) -> bytes:
    """Pretty-printed UTF-8 JSON with trailing newline."""
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _checksums_bytes(cfg: AppConfig, overrides: Mapping[Path, bytes]) -> bytes:
    """Build SHA256SUMS content from live files, applying in-memory overrides."""
    rows: list[str] = []
    for file_path in sorted(_checksum_targets(cfg), key=lambda path: path.name):
        if file_path == cfg.path_checksums:
            continue
        if file_path in overrides:
            data = overrides[file_path]
        elif file_path.is_file():
            data = file_path.read_bytes()
        else:
            continue
        rows.append(f"{sha256_bytes(data)}  {file_path.name}")
    return ("\n".join(rows) + "\n").encode("utf-8")


def _publish_ops(
    cfg: AppConfig,
    *,
    state: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
) -> list[Path]:
    """Publish state/validation/checksums as one dirty-subset staging transaction.

    Generation artifacts (feeds + essays.json) are published separately. This
    second transaction keeps ops files consistent with each other under the same
    pipeline lock (RV-017).
    """
    overrides: dict[Path, bytes] = {}
    if state is not None:
        overrides[cfg.path_state] = _json_bytes(state)
    if report is not None:
        overrides[cfg.path_validation] = _json_bytes(report)
    artifacts = dict(overrides)
    artifacts[cfg.path_checksums] = _checksums_bytes(cfg, overrides)
    return publish_artifacts(
        artifacts,
        stage_base=cfg.repo_root,
        backup=True,
        backup_count=cfg.backup_count,
        only_changed=True,
    )


def _generation_artifacts(
    cfg: AppConfig,
    blobs: dict[str, bytes],
    items: tuple[EssayItem, ...],
    *,
    signature: str,
) -> dict[Path, bytes]:
    """Feeds + essays.json co-publish set (no mixed generations)."""
    path_map = _paths(cfg)
    artifacts = {path_map[name]: data for name, data in blobs.items()}
    artifacts[cfg.path_essays] = essays_bytes(
        items,
        source_url=cfg.source_url,
        logical_signature=signature,
    )
    return artifacts


def _run_check_body(
    cfg: AppConfig,
    *,
    write_report: bool = True,
    quiet: bool = False,
    source_sha256: str | None = None,
) -> tuple[int, Any]:
    """Check implementation without acquiring the lock.

    Returns
    -------
    tuple[int, report]
        Exit code and the validation report object.
    """
    items = load_essays(cfg.path_essays, baseline_path=_baseline_path(cfg))
    if not items:
        raise FeedError("No essays catalog found to check.")
    if len(items) < cfg.min_items:
        raise FeedError(f"Catalog has {len(items)} items, below safety floor {cfg.min_items}.")

    public = cfg.require_public_urls()
    missing = [p for p in _paths(cfg).values() if not p.is_file()]
    if missing:
        raise FeedError("Missing feed artifacts: " + ", ".join(str(p) for p in missing))

    rss = cfg.path_rss.read_bytes()
    atom = cfg.path_atom.read_bytes()
    json_feed = cfg.path_json_feed.read_bytes()
    opml = cfg.path_opml.read_bytes()

    results = validate_all_formats(
        items=items,
        rss=rss,
        atom=atom,
        json_feed=json_feed,
        opml=opml,
        min_items=cfg.min_items,
        public=public,
        generator=cfg.generator,
        feed_id=cfg.feed_id,
    )
    blobs = {"rss": rss, "atom": atom, "json_feed": json_feed, "opml": opml}
    report = build_validation_report(
        status="checked",
        items=items,
        source_url=cfg.source_url,
        source_sha256=source_sha256,
        extraction=None,
        changes=None,
        format_hashes=_format_hash_map(_paths(cfg), blobs),
        parity=results["parity"],
    )
    if write_report:
        _publish_ops(cfg, report=report.to_dict())
    if not quiet:
        print(f"VALID: {len(items)} items; all formats passed local validation.")
    return 0, report


def run_check(
    cfg: AppConfig,
    *,
    write_report: bool = True,
    quiet: bool = False,
    already_locked: bool = False,
) -> int:
    """Validate local state and outputs without network access."""
    if already_locked:
        code, _ = _run_check_body(cfg, write_report=write_report, quiet=quiet)
        return code
    with acquire_lock(_lock_path(cfg)):
        code, _ = _run_check_body(cfg, write_report=write_report, quiet=quiet)
        return code


def _run_build_locked(
    cfg: AppConfig,
    *,
    dry_run: bool = False,
    quiet: bool = False,
) -> int:
    items = load_essays(cfg.path_essays, baseline_path=_baseline_path(cfg))
    if not items:
        raise FeedError("No essays catalog available to build.")
    if len(items) < cfg.min_items:
        raise FeedError(f"Catalog has {len(items)} items, below safety floor {cfg.min_items}.")

    public = cfg.require_public_urls()
    feed_updated = max(item.last_changed_at for item in items) if items else utc_now()
    ctx = _make_context(cfg, items, build_updated_at=feed_updated)
    blobs = _render_all(ctx)
    validate_all_formats(
        items=items,
        rss=blobs["rss"],
        atom=blobs["atom"],
        json_feed=blobs["json_feed"],
        opml=blobs["opml"],
        min_items=cfg.min_items,
        public=public,
        generator=cfg.generator,
        feed_id=cfg.feed_id,
    )
    signature = _signature(cfg, items)

    if dry_run:
        if not quiet:
            print(f"DRY RUN: would build {len(items)} items into four formats.")
        return 0

    path_map = _paths(cfg)
    artifacts = _generation_artifacts(cfg, blobs, items, signature=signature)
    written = publish_artifacts(
        artifacts,
        stage_base=cfg.repo_root,
        backup=True,
        backup_count=cfg.backup_count,
        only_changed=True,
    )
    state = load_state(cfg.path_state)
    state_payload = default_state_payload(
        source_url=cfg.source_url,
        etag=state.get("etag") if isinstance(state.get("etag"), str) else None,
        last_modified=(
            state.get("last_modified") if isinstance(state.get("last_modified"), str) else None
        ),
        source_sha256=(
            state.get("source_sha256") if isinstance(state.get("source_sha256"), str) else None
        ),
        min_items_floor=cfg.min_items,
        public_base_url=cfg.public_base_url,
        logical_signature=signature,
        last_status="updated" if written else "unchanged",
        last_built_at=feed_updated,
    )
    # Preserve prior etag/source fields not in default_state_payload merge from load.
    state.update(state_payload)
    results = validate_all_formats(
        items=items,
        rss=blobs["rss"],
        atom=blobs["atom"],
        json_feed=blobs["json_feed"],
        opml=blobs["opml"],
        min_items=cfg.min_items,
        public=public,
        generator=cfg.generator,
        feed_id=cfg.feed_id,
    )
    report = build_validation_report(
        status="updated" if written else "unchanged",
        items=items,
        source_url=cfg.source_url,
        source_sha256=None,
        extraction=None,
        changes=None,
        format_hashes=_format_hash_map(path_map, blobs),
        parity=results["parity"],
    )
    _publish_ops(cfg, state=state, report=report.to_dict())

    if not quiet:
        action = "UPDATED" if written else "UNCHANGED"
        print(f"{action}: built {len(items)} items across four formats.")
    return 0


def run_build(cfg: AppConfig, *, dry_run: bool = False, quiet: bool = False) -> int:
    """Build all formats from persisted canonical data without fetching."""
    with acquire_lock(_lock_path(cfg)):
        return _run_build_locked(cfg, dry_run=dry_run, quiet=quiet)


def run_diff(
    cfg: AppConfig,
    *,
    source_file: Path | None = None,
    quiet: bool = False,
    out: TextIO | None = None,
) -> int:
    """Report proposed changes without writing artifacts."""
    stream = out or sys.stdout
    previous = load_essays(cfg.path_essays, baseline_path=_baseline_path(cfg))

    if source_file is not None:
        body = source_file.read_bytes()
        if len(body) > cfg.max_response_bytes:
            raise FeedError(f"Source file exceeds {cfg.max_response_bytes} bytes.")
        html = decode_source(body)
    else:
        state = load_state(cfg.path_state)
        fetch_result = fetch_source(
            cfg.source_url,
            timeout=cfg.timeout,
            retries=cfg.retries,
            max_bytes=cfg.max_response_bytes,
            state=state,
            conditional=False,
            source_allowed_hosts=cfg.source_allowed_hosts,
        )
        if fetch_result.body is None:
            raise FeedError("Source fetch returned no body.")
        html = decode_source(fetch_result.body)

    extraction = extract_items(
        html,
        base_url=cfg.source_url,
        min_items=cfg.min_items,
        require_protected_external=not cfg.allow_removals,
    )
    changes = reconcile_items(
        previous,
        extraction.items,
        allow_removals=cfg.allow_removals,
        allow_nonprefix_additions=cfg.allow_nonprefix_additions,
    )
    if not quiet:
        stream.write(
            f"DIFF: +{len(changes.added)} -{len(changes.removed)} "
            f"titleΔ{len(changes.title_changed)} urlΔ{len(changes.url_changed)} "
            f"order_changed={changes.order_changed}\n"
        )
        for identity in changes.added:
            stream.write(f"  + {identity}\n")
        for identity in changes.removed:
            stream.write(f"  - {identity}\n")
    return 0


def _run_update_locked(
    cfg: AppConfig,
    *,
    source_file: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    quiet: bool = False,
) -> int:
    public = cfg.require_public_urls()
    previous = load_essays(cfg.path_essays, baseline_path=_baseline_path(cfg))
    state = load_state(cfg.path_state)
    fetch_result = None
    source_url = cfg.source_url

    if source_file is not None:
        body = source_file.read_bytes()
        if len(body) > cfg.max_response_bytes:
            raise FeedError(f"Source file exceeds {cfg.max_response_bytes} bytes.")
        source_sha256 = sha256_bytes(body)
        html = decode_source(body)
    else:
        fetch_result = fetch_source(
            cfg.source_url,
            timeout=cfg.timeout,
            retries=cfg.retries,
            max_bytes=cfg.max_response_bytes,
            state=state,
            conditional=(not force and cfg.path_rss.exists() and cfg.path_essays.exists()),
            source_allowed_hosts=cfg.source_allowed_hosts,
        )
        if fetch_result.not_modified:
            try:
                code, _ = _run_check_body(cfg, write_report=not dry_run, quiet=quiet)
                return code
            except FeedError:
                fetch_result = fetch_source(
                    cfg.source_url,
                    timeout=cfg.timeout,
                    retries=cfg.retries,
                    max_bytes=cfg.max_response_bytes,
                    state={},
                    conditional=False,
                    source_allowed_hosts=cfg.source_allowed_hosts,
                )
        if fetch_result.body is None:
            raise FeedError("Source fetch returned no body.")
        body = fetch_result.body
        source_sha256 = sha256_bytes(body)
        source_url = fetch_result.final_url
        html = decode_source(body)

    extraction = extract_items(
        html,
        base_url=cfg.source_url,
        min_items=cfg.min_items,
        require_protected_external=not cfg.allow_removals,
    )
    changes: ChangeSet = reconcile_items(
        previous,
        extraction.items,
        allow_removals=cfg.allow_removals,
        allow_nonprefix_additions=cfg.allow_nonprefix_additions,
    )
    items = merge_items(previous, extraction.items)
    signature = _signature(cfg, items)
    prior_signature = None
    essays_payload_data = load_optional_json(cfg.path_essays)
    if isinstance(essays_payload_data.get("logical_signature_sha256"), str):
        prior_signature = essays_payload_data["logical_signature_sha256"]
    elif previous:
        prior_signature = _signature(cfg, previous)

    logical_change = prior_signature != signature or force

    if not logical_change and not force:
        if dry_run:
            if not quiet:
                print(f"UNCHANGED: {len(items)} items; no logical content change.")
            return 0
        missing = [p for p in _paths(cfg).values() if not p.is_file()]
        if missing:
            raise FeedError(
                "Logical content unchanged but feed artifacts missing:\n  "
                + "\n  ".join(str(p) for p in missing)
            )
        # Validate without writing; publish state+report+checksums together.
        _, report_u = _run_check_body(
            cfg, write_report=False, quiet=True, source_sha256=source_sha256
        )
        state_payload = default_state_payload(
            source_url=cfg.source_url,
            etag=fetch_result.etag if fetch_result else state.get("etag"),
            last_modified=(
                fetch_result.last_modified if fetch_result else state.get("last_modified")
            ),
            source_sha256=source_sha256,
            min_items_floor=cfg.min_items,
            public_base_url=cfg.public_base_url,
            logical_signature=signature,
            last_status="unchanged",
        )
        if isinstance(state.get("last_built_at"), str):
            state_payload["last_built_at"] = state["last_built_at"]
        _publish_ops(cfg, state=state_payload, report=report_u.to_dict())
        if not quiet:
            print(f"UNCHANGED: {len(items)} valid items; no feed rewrite.")
        return 0

    feed_updated = utc_now()
    ctx = _make_context(cfg, items, build_updated_at=feed_updated)
    blobs = _render_all(ctx)
    results = validate_all_formats(
        items=items,
        rss=blobs["rss"],
        atom=blobs["atom"],
        json_feed=blobs["json_feed"],
        opml=blobs["opml"],
        min_items=cfg.min_items,
        public=public,
        generator=cfg.generator,
        feed_id=cfg.feed_id,
    )

    if dry_run:
        if not quiet:
            print(
                f"DRY RUN: would write {len(items)} items "
                f"({len(changes.added)} added, {len(changes.removed)} removed)."
            )
        return 0

    path_map = _paths(cfg)
    artifacts = _generation_artifacts(cfg, blobs, items, signature=signature)
    publish_artifacts(
        artifacts,
        stage_base=cfg.repo_root,
        backup=True,
        backup_count=cfg.backup_count,
        only_changed=True,
    )
    state_payload = default_state_payload(
        source_url=cfg.source_url,
        etag=fetch_result.etag if fetch_result else None,
        last_modified=fetch_result.last_modified if fetch_result else None,
        source_sha256=source_sha256,
        min_items_floor=cfg.min_items,
        public_base_url=cfg.public_base_url,
        logical_signature=signature,
        last_status="updated",
        last_built_at=feed_updated,
    )
    report = build_validation_report(
        status="updated",
        items=items,
        source_url=source_url,
        source_sha256=source_sha256,
        extraction=extraction_meta_dict(extraction),
        changes=changes.to_dict(),
        format_hashes=_format_hash_map(path_map, blobs),
        parity=results["parity"],
    )
    _publish_ops(cfg, state=state_payload, report=report.to_dict())

    if not quiet:
        print(
            f"UPDATED: wrote {len(items)} items; "
            f"+{len(changes.added)} -{len(changes.removed)}; validation passed."
        )
    return 0


def run_update(
    cfg: AppConfig,
    *,
    source_file: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    quiet: bool = False,
) -> int:
    """Fetch (or read file), extract, reconcile, build, validate, publish."""
    with acquire_lock(_lock_path(cfg)):
        return _run_update_locked(
            cfg,
            source_file=source_file,
            dry_run=dry_run,
            force=force,
            quiet=quiet,
        )
