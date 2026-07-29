"""Page enrich (selectolax metadata) + live link probes.

Absorbs former ``metadata`` / ``validate`` modules (T7). HTML parsing uses
selectolax only; marker/allowlist policy for the index lives in ``discover``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Final, Literal
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
from selectolax.parser import HTMLParser, Node

from paul_graham_essay_feeds.http import (
    ResultKind,
    conditional_headers,
    create_http_client,
    decode_html,
    decode_html_document,
    get_with_evidence,
    hop_safe_get,
    hop_safe_request,
    run_with_retry,
)
from paul_graham_essay_feeds.models import (
    ALLOWED_HOSTS,
    FEED_SUMMARY_CHARS,
    MAX_BYTES,
    NULL_REPORTER,
    Essay,
    FeedError,
    OutputPolicy,
    ProgressReporter,
    content_sha256,
    normalize_text,
    truncate_text,
    user_agent,
    validate_essay_link,
)

# Test / adapter aliases (summary cap SSOT is FEED_SUMMARY_CHARS).
_SUMMARY_CHARS = FEED_SUMMARY_CHARS
_MAX_CONTENT_CHARS = 600

_USER_AGENT = user_agent(" link-check")

# Month+year on the page is a human hint only (AD-003); never invent day-1 dates.
_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{4})\b",
    re.I,
)

_SKIP_TAGS: Final = frozenset({"script", "style", "noscript", "svg", "template"})
_CHROME_TAGS: Final = frozenset({"nav", "footer", "header", "aside", "form", "menu"})
_CHROME_ATTR = re.compile(
    r"(?:\bnav\b|footer|header|promo|subscribe|sidebar|cookie|banner|menu|"
    r"newsletter|site-nav|topbar|yc-promo)",
    re.I,
)
_PROMO_LINE = re.compile(
    r"(want to start a startup|get funded by y combinator|"
    r"subscribe|click here|sign up for|join our newsletter|"
    r"follow us|share this)",
    re.I,
)
_NAV_LIKE = re.compile(
    r"(want to start a startup|get funded by y combinator|"
    r"\bhome\b\s*[|/]\s*\babout\b|\bmenu\b|\bnavigation\b)",
    re.I,
)

_MIN_PARAGRAPH_CHARS: Final = 40
_MIN_GOOD_SUMMARY_CHARS: Final = 40
_MAX_SCAN_CHARS: Final = 2_000

SummarySource = Literal[
    "meta_description",
    "og_description",
    "twitter_description",
    "content_paragraph",
]


# --- Page metadata (former metadata.py) --------------------------------------


class PageMetadata(BaseModel):
    """Short, source-derived page metadata for feed enrichment.

    Does **not** carry ``published_at``: month+year text is ``published_hint`` only.
    Does **not** retain full essay bodies — ``summary`` is always ≤ ``FEED_SUMMARY_CHARS``.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    page_title: str | None = Field(
        default=None,
        description="Normalized HTML <title> text, if present.",
    )
    meta_description: str | None = Field(
        default=None,
        description='Normalized <meta name="description"> content, if present.',
    )
    og_title: str | None = Field(
        default=None,
        description="Normalized og:title meta content, if present.",
    )
    og_description: str | None = Field(
        default=None,
        description="Normalized og:description meta content, if present.",
    )
    canonical_url: str | None = Field(
        default=None,
        description=(
            "Absolute URL from rel=canonical (token list aware). "
            "Hint only — never used as automatic identity rewrite."
        ),
    )
    published_hint: str | None = Field(
        default=None,
        description='Month+year human hint (e.g. "June 2026"); never a full calendar day.',
    )
    summary: str | None = Field(
        default=None,
        description=(
            "Short description for feeds (≤ FEED_SUMMARY_CHARS). "
            "From meta / og / first content paragraph; never a full essay body."
        ),
    )
    summary_source: SummarySource | None = Field(
        default=None,
        description=(
            "Provenance of summary: meta_description, og_description, "
            "twitter_description, or content_paragraph."
        ),
    )
    quality_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Heuristic quality in [0, 1]; lower when empty, short, promo, or U+FFFD.",
    )
    quality_flags: tuple[str, ...] = Field(
        default=(),
        description=(
            "Stable quality flag tokens (empty, too_short, subscribe, click_here, "
            "nav_like, replacement_char)."
        ),
    )

    @field_validator("summary")
    @classmethod
    def _cap_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        capped = truncate_text(value, FEED_SUMMARY_CHARS)
        return capped or None


def _attrs_look_like_chrome(amap: Mapping[str, str | None]) -> bool:
    role = (amap.get("role") or "").lower()
    if role in {"navigation", "banner", "contentinfo", "complementary"}:
        return True
    return any(_CHROME_ATTR.search(amap.get(key) or "") for key in ("id", "class", "aria-label"))


def _optional_norm(value: str | None) -> str | None:
    if not value:
        return None
    text = normalize_text(value)
    return text or None


def _resolve_canonical(href: str | None, *, page_url: str) -> str | None:
    """Resolve canonical href against page_url; return absolute string or None."""
    if not href:
        return None
    absolute = urljoin(page_url, href.strip())
    if not absolute:
        return None
    return absolute


def _is_promo_or_chrome(text: str) -> bool:
    if not text:
        return True
    if _PROMO_LINE.search(text):
        return True
    return len(text) < 12 and text.lower() in {"home", "about", "essays", "rss", "index"}


def _first_content_paragraph(
    paragraphs: list[str],
    loose_body: str,
    *,
    page_title: str | None,
) -> str | None:
    """Pick the first non-promo content paragraph (or cleaned loose body)."""
    for para in paragraphs:
        if len(para) < _MIN_PARAGRAPH_CHARS:
            continue
        if _is_promo_or_chrome(para):
            continue
        return para

    body = loose_body
    if page_title and body.startswith(page_title):
        body = body[len(page_title) :].lstrip(" -|:")
    if not body:
        return None

    parts = re.split(r"(?<=[.!?])\s+", body)
    kept: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if _is_promo_or_chrome(part) and not kept:
            continue
        kept.append(part)
        joined = " ".join(kept)
        if len(joined) >= _MIN_PARAGRAPH_CHARS:
            if len(joined) > _MAX_SCAN_CHARS:
                return joined[:_MAX_SCAN_CHARS]
            return joined

    joined = " ".join(kept) if kept else body
    if not joined or _is_promo_or_chrome(joined):
        cleaned = body
        for promo in _PROMO_LINE.finditer(body):
            if promo.start() < 40:
                cleaned = body[promo.end() :].lstrip(" .-|:")
                break
        joined = cleaned or body
    if len(joined) > _MAX_SCAN_CHARS:
        joined = joined[:_MAX_SCAN_CHARS]
    return joined or None


def _published_hint_from_text(*chunks: str | None) -> str | None:
    for chunk in chunks:
        if not chunk:
            continue
        head = chunk[:400]
        match = _MONTH_YEAR.search(head)
        if match:
            return f"{match.group(1).title()} {match.group(2)}"
    return None


def score_summary_quality(summary: str | None) -> tuple[float, tuple[str, ...]]:
    """Return ``(quality_score, quality_flags)`` for a candidate summary."""
    if summary is None or not summary.strip():
        return 0.0, ("empty",)

    text = summary.strip()
    flags: list[str] = []
    score = 1.0

    if len(text) < _MIN_GOOD_SUMMARY_CHARS:
        score -= 0.35
        flags.append("too_short")

    lower = text.lower()
    if "subscribe" in lower:
        score -= 0.40
        flags.append("subscribe")
    if "click here" in lower:
        score -= 0.30
        flags.append("click_here")
    if _NAV_LIKE.search(text):
        score -= 0.40
        flags.append("nav_like")
    if "\ufffd" in text:
        score -= 0.50
        flags.append("replacement_char")

    score = max(0.0, min(1.0, score))
    return score, tuple(flags)


def _collect_meta_map(tree: HTMLParser) -> dict[str, str]:
    """First-wins meta name/property → content map."""
    meta: dict[str, str] = {}
    for node in tree.css("meta"):
        attrs = node.attributes or {}
        name = (attrs.get("name") or attrs.get("property") or "").lower().strip()
        content = (attrs.get("content") or "").strip()
        if name and content and name not in meta:
            meta[name] = content
    return meta


def _canonical_href(tree: HTMLParser) -> str | None:
    for node in tree.css("link"):
        attrs = node.attributes or {}
        rel_tokens = (attrs.get("rel") or "").lower().split()
        if "canonical" not in rel_tokens:
            continue
        href = (attrs.get("href") or "").strip()
        if href:
            return href
    return None


def _is_text_node(tag: str | None) -> bool:
    """selectolax marks text nodes as ``-text`` (lexbor); tolerate ``None``."""
    return tag is None or tag == "-text"


def _node_text_chunk(node: Node) -> str:
    raw = node.text_content or ""
    return " ".join(raw.split())


def _paragraph_text(node: Node) -> str:
    """Paragraph text with ``<br>`` / ``<hr>`` treated as whitespace."""
    parts: list[str] = []

    def walk(n: Node) -> None:
        for child in n.iter(include_text=True):
            tag = child.tag
            if _is_text_node(tag):
                text = _node_text_chunk(child)
                if text:
                    parts.append(text)
                continue
            assert tag is not None
            tag_l = tag.lower()
            if tag_l in {"br", "hr"}:
                parts.append(" ")
                continue
            if tag_l in _SKIP_TAGS:
                continue
            walk(child)

    walk(node)
    return normalize_text(" ".join(parts))


def _collect_content_texts(tree: HTMLParser) -> tuple[list[str], str]:
    """Chrome-filtered content paragraphs + loose body text."""
    paragraphs: list[str] = []
    loose_parts: list[str] = []
    root = tree.body or tree.root
    if root is None:
        return paragraphs, ""

    def walk(node: Node) -> None:
        tag = node.tag
        if _is_text_node(tag):
            return
        assert tag is not None
        tag_l = tag.lower()
        if tag_l in _SKIP_TAGS or tag_l == "title":
            return
        attrs = node.attributes or {}
        if tag_l in _CHROME_TAGS or _attrs_look_like_chrome(attrs):
            # Skip entire chrome subtree (void chrome tags have no children).
            return
        if tag_l == "p":
            text = _paragraph_text(node)
            if text:
                paragraphs.append(text)
            return
        for child in node.iter(include_text=True):
            child_tag = child.tag
            if _is_text_node(child_tag):
                text = _node_text_chunk(child)
                if text:
                    loose_parts.append(text)
                continue
            walk(child)

    walk(root)
    return paragraphs, normalize_text(" ".join(loose_parts))


def _allowlisted_image(url: str | None, *, page_url: str) -> str | None:
    if not url:
        return None
    image = urljoin(page_url, url.strip())
    parts = urlsplit(image)
    host = (parts.hostname or "").lower()
    if host == "www.paulgraham.com":
        host = "paulgraham.com"
    if parts.scheme != "https" or host not in ALLOWED_HOSTS:
        return None
    return image


@dataclass(frozen=True, slots=True)
class _ParsedPage:
    """One selectolax pass: metadata + image/keywords extras."""

    metadata: PageMetadata
    image_url: str | None
    keywords: str | None


def _parse_page(html: str, *, page_url: str) -> _ParsedPage:
    tree = HTMLParser(html)
    meta = _collect_meta_map(tree)

    title_node = tree.css_first("title")
    page_title = _optional_norm(title_node.text(separator=" ")) if title_node else None
    meta_description = _optional_norm(meta.get("description"))
    og_title = _optional_norm(meta.get("og:title"))
    og_description = _optional_norm(meta.get("og:description"))
    twitter_description = _optional_norm(meta.get("twitter:description"))
    canonical_url = _resolve_canonical(_canonical_href(tree), page_url=page_url)

    paragraphs, loose = _collect_content_texts(tree)
    content_paragraph = _first_content_paragraph(
        paragraphs,
        loose,
        page_title=page_title,
    )

    early_scan = " ".join(paragraphs[:8] + ([loose] if loose else []))
    published_hint = _published_hint_from_text(
        early_scan,
        content_paragraph,
        meta_description,
        og_description,
    )

    summary: str | None = None
    summary_source: SummarySource | None = None
    if meta_description:
        summary = meta_description
        summary_source = "meta_description"
    elif og_description:
        summary = og_description
        summary_source = "og_description"
    elif twitter_description:
        summary = twitter_description
        summary_source = "twitter_description"
    elif content_paragraph:
        summary = content_paragraph
        summary_source = "content_paragraph"

    if summary:
        summary = truncate_text(summary, FEED_SUMMARY_CHARS) or None

    def _cap(value: str | None) -> str | None:
        if not value:
            return value
        return truncate_text(value, FEED_SUMMARY_CHARS) or None

    quality_score, quality_flags = score_summary_quality(summary)
    page = PageMetadata(
        page_title=_cap(page_title),
        meta_description=_cap(meta_description),
        og_title=_cap(og_title),
        og_description=_cap(og_description),
        canonical_url=canonical_url,
        published_hint=published_hint,
        summary=summary,
        summary_source=summary_source,
        quality_score=quality_score,
        quality_flags=quality_flags,
    )

    image = _allowlisted_image(
        meta.get("og:image") or meta.get("twitter:image"),
        page_url=page_url,
    )
    keywords = meta.get("keywords") or meta.get("news_keywords") or None
    if keywords:
        keywords = normalize_text(keywords) or None

    return _ParsedPage(metadata=page, image_url=image, keywords=keywords)


def extract_page_metadata(html: str | bytes, *, page_url: str) -> PageMetadata:
    """Extract content-root metadata and a short quality-scored summary."""
    text = decode_html_document(html).text if isinstance(html, bytes) else html
    return _parse_page(text, page_url=page_url).metadata


# --- Enrich transport --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageEnrichEvidence:
    """Per-page enrich transport evidence (validators + 304 flag)."""

    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class _PageGet:
    """Internal page GET outcome (body optional on 304)."""

    html: str | None
    not_modified: bool
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None


def parse_page_metadata(html: str, *, page_url: str) -> dict[str, str | None]:
    """Extract short summary fields plus allowlisted image/keywords.

    Kept as a dict adapter for enrich callers and characterization tests.
    """
    parsed = _parse_page(html, page_url=page_url)
    page = parsed.metadata
    return {
        "page_title": page.page_title,
        "summary": page.summary,
        "content_text": None,
        "image_url": parsed.image_url,
        "keywords": parsed.keywords,
        "canonical_url": page.canonical_url,
        "published_hint": page.published_hint,
        "published_at": None,
    }


def _fetch_page(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int,
    etag: str | None = None,
    last_modified: str | None = None,
) -> _PageGet:
    """GET essay page with hop-safe redirects and optional conditional validators."""
    cond = conditional_headers(etag=etag, last_modified=last_modified) or None
    result = get_with_evidence(
        client,
        url,
        allowed_hosts=ALLOWED_HOSTS,
        max_bytes=max_bytes,
        headers=cond,
    )
    ev = result.evidence
    if ev.result_kind is ResultKind.NOT_MODIFIED or ev.status_code == 304:
        return _PageGet(
            html=None,
            not_modified=True,
            etag=ev.etag or etag,
            last_modified=ev.last_modified or last_modified,
            status_code=304,
        )
    if result.response is None:
        raise FeedError(ev.error_message or f"Fetch failed for {url}")
    result.response.raise_for_status()
    html = decode_html(result.body, transport_charset=ev.charset)
    return _PageGet(
        html=html,
        not_modified=False,
        etag=ev.etag,
        last_modified=ev.last_modified,
        status_code=ev.status_code,
    )


def _enrich_one(
    client: httpx.Client,
    essay: Essay,
    *,
    max_bytes: int,
    attempts: int,
    etag: str | None = None,
    last_modified: str | None = None,
) -> tuple[Essay, PageEnrichEvidence | None]:
    """Scrape one page; soft-fail to the original essay on network/parse errors."""

    def _load() -> _PageGet:
        return _fetch_page(
            client,
            essay.url,
            max_bytes=max_bytes,
            etag=etag,
            last_modified=last_modified,
        )

    try:
        fetched = run_with_retry(_load, attempts=attempts, what=f"enrich {essay.url}")
    except FeedError as exc:
        logger.warning("Enrichment failed for {}: {}", essay.url, exc)
        return essay, None

    evidence = PageEnrichEvidence(
        not_modified=fetched.not_modified,
        etag=fetched.etag,
        last_modified=fetched.last_modified,
        status_code=fetched.status_code,
    )
    if fetched.not_modified:
        return essay, evidence

    assert fetched.html is not None
    page_hash = content_sha256(fetched.html)
    meta = parse_page_metadata(fetched.html, page_url=essay.url)

    updated = essay.model_copy(
        update={
            "page_title": meta.get("page_title"),
            "summary": meta.get("summary"),
            "content_text": None,
            "image_url": meta.get("image_url"),
            "keywords": meta.get("keywords"),
            "canonical_url": meta.get("canonical_url") or essay.url,
            "published_hint": meta.get("published_hint"),
            "published_at": None,
            "content_hash": page_hash,
        }
    )
    return updated, evidence


def enrich_essays(
    essays: list[Essay],
    *,
    timeout: float = 15.0,
    workers: int = 4,
    retries: int = 2,
    max_bytes: int = 2 * 1024 * 1024,
    quiet: bool = False,
    page_validators: Mapping[str, tuple[str | None, str | None]] | None = None,
    page_evidence_out: MutableMapping[str, PageEnrichEvidence] | None = None,
) -> list[Essay]:
    """Fetch each essay page and attach short summaries (order preserved)."""
    if not essays:
        return essays
    attempts = max(1, retries + 1)
    validators = page_validators or {}
    out: dict[int, Essay] = {}
    with (
        create_http_client(
            timeout=timeout,
            accept="text/html,text/plain,*/*;q=0.8",
        ) as client,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        futures = {
            pool.submit(
                _enrich_one,
                client,
                e,
                max_bytes=max_bytes,
                attempts=attempts,
                etag=validators.get(e.stable_id, (None, None))[0],
                last_modified=validators.get(e.stable_id, (None, None))[1],
            ): e
            for e in essays
        }
        reporter = ProgressReporter(OutputPolicy(quiet=quiet))
        for fut in reporter.track(
            as_completed(futures),
            total=len(futures),
            desc="Enrich pages",
            unit="page",
        ):
            prior = futures[fut]
            pos = prior.position
            try:
                essay, evidence = fut.result()
            except Exception as exc:
                logger.warning("Enrich worker failed for position {}: {}", pos, exc)
                out[pos] = prior
                continue
            out[pos] = essay
            if page_evidence_out is not None and evidence is not None:
                page_evidence_out[essay.stable_id] = evidence

    enriched = [out[e.position] for e in essays]
    ok = sum(1 for e in enriched if e.summary)
    logger.info("Enriched {}/{} essays with page metadata", ok, len(enriched))
    return enriched


# --- Link validation (former validate.py) ------------------------------------


@dataclass(frozen=True, slots=True)
class LinkProbeReport:
    """Outcome of live link probes (never fails the update by itself)."""

    checked: int
    ok: int
    failures: tuple[str, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> int:
        return len(self.failures)


def validate_essays_structural(
    essays: list[Essay],
    *,
    reporter: ProgressReporter | None = None,
) -> None:
    """Always-on validation for every included link."""
    progress = reporter or NULL_REPORTER
    if len(essays) < 20:
        iterable = essays
    else:
        iterable = progress.track(essays, desc="Validate links", unit="url")
    for essay in iterable:
        validate_essay_link(essay)
    logger.info("Structural link validation OK ({} urls)", len(essays))


def _probe_once(client: httpx.Client, essay: Essay, *, max_bytes: int) -> None:
    """Single probe attempt; raises httpx errors for tenacity."""
    response = hop_safe_request(
        client,
        "HEAD",
        essay.url,
        allowed_hosts=ALLOWED_HOSTS,
        max_bytes=max_bytes,
        allow_loopback=None,
    )
    if response.status_code in {405, 501}:
        response = hop_safe_get(
            client,
            essay.url,
            allowed_hosts=ALLOWED_HOSTS,
            max_bytes=max_bytes,
            allow_loopback=None,
        )
    if response.status_code >= 400:
        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
            response.raise_for_status()
        raise FeedError(f"{essay.url} → HTTP {response.status_code}")
    host = urlsplit(str(response.url)).hostname or ""
    host_l = host.lower()
    if host_l == "www.paulgraham.com":
        host_l = "paulgraham.com"
    if host_l not in ALLOWED_HOSTS:
        raise FeedError(f"{essay.url} redirected to disallowed host {host!r}")


def _probe_one(
    client: httpx.Client,
    essay: Essay,
    *,
    attempts: int,
    max_bytes: int,
) -> str | None:
    """Return error message or None if OK (retries transient failures)."""
    try:
        run_with_retry(
            lambda: _probe_once(client, essay, max_bytes=max_bytes),
            attempts=attempts,
            what=f"probe {essay.url}",
        )
    except FeedError as exc:
        return str(exc)
    except httpx.HTTPError as exc:
        return f"{essay.url} → {exc}"
    return None


def validate_essays_live(
    essays: list[Essay],
    *,
    timeout: float = 10.0,
    workers: int = 4,
    retries: int = 2,
    max_bytes: int = MAX_BYTES,
    quiet: bool = False,
    reporter: ProgressReporter | None = None,
) -> LinkProbeReport:
    """Live-probe each essay URL; report failures without raising or dropping items."""
    if not essays:
        return LinkProbeReport(checked=0, ok=0, failures=())

    errors: list[str] = []
    attempts = max(1, retries + 1)
    progress = reporter or ProgressReporter(OutputPolicy(quiet=quiet))
    with (
        httpx.Client(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": _USER_AGENT},
        ) as client,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        futures = {
            pool.submit(
                _probe_one,
                client,
                e,
                attempts=attempts,
                max_bytes=max_bytes,
            ): e
            for e in essays
        }
        for fut in progress.track(
            as_completed(futures),
            total=len(futures),
            desc="Probe links",
            unit="url",
        ):
            err = fut.result()
            if err:
                errors.append(err)
                logger.warning("Link probe issue: {}", err)

    failures = tuple(errors)
    ok = len(essays) - len(failures)
    report = LinkProbeReport(checked=len(essays), ok=ok, failures=failures)
    if failures:
        preview = "\n  ".join(failures[:10])
        more = f"\n  … +{len(failures) - 10} more" if len(failures) > 10 else ""
        logger.warning(
            "{} link probe failure(s) (essays still included):\n  {}{}",
            len(failures),
            preview,
            more,
        )
    else:
        logger.info("Live link probes OK ({} urls)", len(essays))
    return report
