"""Page enrich (selectolax metadata) + live link probes.

Absorbs former ``metadata`` / ``validate`` modules (T7). HTML parsing uses
selectolax only; marker/allowlist policy for the index lives in ``discover``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
from selectolax.parser import HTMLParser, Node

from paul_graham_essay_feeds.http import (
    HostCooldown,
    ResultKind,
    conditional_headers,
    create_http_client,
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
    SUMMARY_QUALITY_THRESHOLD,
    Essay,
    FeedError,
    OutputPolicy,
    ProgressReporter,
    SummarySource,
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

# Stable tokens for notebook/status aggregation (PGF-P1-004). Keep the
# existing ``Link probe issue:`` line as well — tests and logs may depend on it.
REACHABILITY_FAIL_TOKEN: Final = "PGF_REACHABILITY_FAIL"
ENRICH_DEGRADED_TOKEN: Final = "PGF_ENRICH_DEGRADED"
_SOURCE_PRIORITY: Final[dict[SummarySource, int]] = {
    "meta_description": 0,
    "og_description": 1,
    "twitter_description": 2,
    "content_paragraph": 3,
    "title": 4,
}

# Month+year on the page is a human hint only (AD-003); never invent day-1 dates.
_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{4})\b",
    re.I,
)

_SKIP_TAGS: Final = frozenset({"script", "style", "noscript", "svg", "template"})
_CHROME_TAGS: Final = frozenset({"nav", "footer", "header", "aside", "form", "menu"})
_CHROME_CONTAINER_TAGS: Final = frozenset({"table", "tr", "td", "font", "div", "center"})
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
_YC_BANNER = re.compile(
    r"(?:want to start a startup\??\s*)?get funded by y combinator\.?",
    re.I,
)
_NAV_LIKE = re.compile(
    r"(\bhome\b\s*[|/]\s*\babout\b|\bmenu\b)",
    re.I,
)
_TRANSLATION_ITEM = re.compile(
    r"\b(?:traditional\s+chinese|simplified\s+chinese|arabic|chinese|japanese|"
    r"russian|korean|spanish|french|german|italian|portuguese|romanian|"
    r"turkish|hebrew|vietnamese|indonesian|polish|dutch|czech|swedish|"
    r"norwegian|danish|finnish|hungarian|thai|hindi|ukrainian|persian|"
    r"farsi|bulgarian|croatian|serbian|greek|catalan|esperanto)\s+translation\b",
    re.I,
)
_DOMAIN_SEARCH = re.compile(r"\bdomain\s+name\s+search\b", re.I)
_BOOK_PROMO = re.compile(
    r"you(?:'|[\u2019])ll find this essay and \d+ others in hackers\s*&\s*painters",
    re.I,
)
_NAV_TOKENS: Final = frozenset({"home", "about", "essays", "rss", "index"})

_MIN_PARAGRAPH_CHARS: Final = 40
_MIN_GOOD_SUMMARY_CHARS: Final = 40
_PROSE_MIN_CHARS: Final = 120
_MAX_SCAN_CHARS: Final = 2_000
_CHROME_LINK_DENSITY: Final = 0.55
_CHROME_MIN_LINKS: Final = 2
_CHROME_LEFTOVER_CHARS: Final = 40

SEMANTIC_FAIL_FLAGS: Final = frozenset(
    {
        "empty",
        "translation_menu",
        "promo",
        "nav_like",
        "domain_search",
        "book_promo",
        "related_links",
        "high_link_density",
    }
)


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
            "twitter_description, content_paragraph, or title."
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
            "promo, nav_like, translation_menu, domain_search, book_promo, "
            "related_links, high_link_density, replacement_char)."
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


def _strip_chrome_tokens(text: str) -> str:
    """Remove known chrome phrases; leftover is candidate prose."""
    stripped = _TRANSLATION_ITEM.sub(" ", text)
    stripped = _DOMAIN_SEARCH.sub(" ", stripped)
    stripped = _BOOK_PROMO.sub(" ", stripped)
    stripped = _YC_BANNER.sub(" ", stripped)
    return normalize_text(stripped)


def _is_translation_menu(text: str) -> bool:
    items = _TRANSLATION_ITEM.findall(text)
    if not items:
        return False
    leftover = _strip_chrome_tokens(text)
    if len(leftover) < _CHROME_LEFTOVER_CHARS:
        return True
    joined_len = sum(len(item) for item in items)
    return joined_len / max(len(text), 1) >= 0.4


def _looks_like_link_list(text: str) -> bool:
    if not text or len(text) > 160:
        return False
    if re.search(r"[.!?]", text):
        return False
    words = text.split()
    if len(words) < 2:
        return False
    capped = sum(1 for word in words if word[:1].isupper())
    return capped / len(words) >= 0.7


def _is_mostly_chrome(text: str) -> bool:
    leftover = _strip_chrome_tokens(text)
    return len(leftover) < max(_CHROME_LEFTOVER_CHARS, int(0.25 * len(text)))


def _is_chrome_block(text: str) -> bool:
    """True when *text itself* is chrome, not essay prose that mentions chrome phrases."""
    if not text or not text.strip():
        return True
    raw = text.strip()
    if len(raw) < 12 and raw.lower() in _NAV_TOKENS:
        return True
    if _is_translation_menu(raw):
        return True
    leftover = _strip_chrome_tokens(raw)
    if _looks_like_link_list(leftover or raw) and len(leftover) < 80:
        return True
    if len(raw) >= _PROSE_MIN_CHARS and not _is_mostly_chrome(raw):
        return False
    if _DOMAIN_SEARCH.search(raw) or _BOOK_PROMO.search(raw) or _YC_BANNER.search(raw):
        return True
    if _PROMO_LINE.search(raw) or _NAV_LIKE.search(raw):
        return True
    return leftover != raw and len(leftover) < _CHROME_LEFTOVER_CHARS


def _chrome_quality_flags(text: str) -> tuple[str, ...]:
    """Flags for chrome-like candidates; long essay prose is not flagged as promo."""
    flags: list[str] = []
    mostly = _is_mostly_chrome(text)
    short = len(text) < _PROSE_MIN_CHARS
    apply_chrome = short or mostly
    if _is_translation_menu(text):
        flags.append("translation_menu")
    if apply_chrome and _DOMAIN_SEARCH.search(text):
        flags.append("domain_search")
    if apply_chrome and _BOOK_PROMO.search(text):
        flags.append("book_promo")
    if apply_chrome and (_YC_BANNER.search(text) or _PROMO_LINE.search(text)):
        flags.append("promo")
    if apply_chrome and _NAV_LIKE.search(text):
        flags.append("nav_like")
    leftover = _strip_chrome_tokens(text)
    if apply_chrome and _looks_like_link_list(leftover or text):
        flags.append("related_links")
    if apply_chrome and leftover != text and _looks_like_link_list(leftover or text):
        flags.append("high_link_density")
    return tuple(dict.fromkeys(flags))


def _first_content_paragraph(
    paragraphs: list[str],
    loose_body: str,
    *,
    page_title: str | None,
) -> str | None:
    """Pick the first non-chrome content paragraph (or cleaned loose body)."""
    for para in paragraphs:
        if len(para) < _MIN_PARAGRAPH_CHARS:
            continue
        if _is_chrome_block(para):
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
        if _is_chrome_block(part) and not kept:
            continue
        kept.append(part)
        joined = " ".join(kept)
        if len(joined) >= _MIN_PARAGRAPH_CHARS and not _is_chrome_block(joined):
            if len(joined) > _MAX_SCAN_CHARS:
                return joined[:_MAX_SCAN_CHARS]
            return joined

    joined = " ".join(kept) if kept else body
    if not joined or _is_chrome_block(joined):
        cleaned = body
        for promo in _PROMO_LINE.finditer(body):
            if promo.start() < 40:
                cleaned = body[promo.end() :].lstrip(" .-|:")
                break
        joined = cleaned or body
    if len(joined) > _MAX_SCAN_CHARS:
        joined = joined[:_MAX_SCAN_CHARS]
    if not joined or _is_chrome_block(joined):
        return None
    return joined


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
    if "\ufffd" in text:
        score -= 0.50
        flags.append("replacement_char")

    chrome_flags = _chrome_quality_flags(text)
    for flag in chrome_flags:
        flags.append(flag)
        if flag == "translation_menu":
            score -= 0.55
        elif flag in {"promo", "nav_like", "high_link_density"}:
            score -= 0.50
        else:
            score -= 0.45

    score = max(0.0, min(1.0, score))
    return score, tuple(dict.fromkeys(flags))


def summary_passes_quality_gate(
    summary: str | None,
    *,
    score: float | None = None,
    flags: tuple[str, ...] | None = None,
) -> bool:
    """True when a candidate is usable as an enriched feed summary."""
    if summary is None or not summary.strip():
        return False
    if score is None or flags is None:
        score, flags = score_summary_quality(summary)
    if score < SUMMARY_QUALITY_THRESHOLD:
        return False
    return not SEMANTIC_FAIL_FLAGS.intersection(flags)


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


def _node_is_chrome_container(node: Node) -> bool:
    """True for translation/promo/nav tables that do not hold essay ``<p>`` prose."""
    tag = node.tag
    if not tag or tag.lower() not in _CHROME_CONTAINER_TAGS:
        return False
    for para in node.css("p"):
        text = _paragraph_text(para)
        if len(text) >= _PROSE_MIN_CHARS:
            return False
        if len(text) >= _MIN_PARAGRAPH_CHARS and not _is_chrome_block(text):
            return False
    text = normalize_text(node.text(separator=" ") or "")
    if not text:
        return False
    links = node.css("a")
    n_links = len(links)
    link_chars = 0
    for anchor in links:
        link_chars += len(normalize_text(anchor.text(separator=" ") or ""))
    density = link_chars / max(len(text), 1)
    if n_links >= _CHROME_MIN_LINKS and density >= _CHROME_LINK_DENSITY:
        return True
    return _is_chrome_block(text)


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
        if _node_is_chrome_container(node):
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

    ranked: list[tuple[int, SummarySource, str]] = []
    for source, text in (
        ("meta_description", meta_description),
        ("og_description", og_description),
        ("twitter_description", twitter_description),
        ("content_paragraph", content_paragraph),
    ):
        if not text:
            continue
        clipped = truncate_text(text, FEED_SUMMARY_CHARS) or None
        if not clipped:
            continue
        ranked.append((_SOURCE_PRIORITY[source], source, clipped))

    summary: str | None = None
    summary_source: SummarySource | None = None
    if ranked:
        scored: list[tuple[float, int, SummarySource, str, tuple[str, ...]]] = []
        for priority, source, text in ranked:
            score, flags = score_summary_quality(text)
            if not summary_passes_quality_gate(text, score=score, flags=flags):
                continue
            scored.append((score, -priority, source, text, flags))
        if scored:
            scored.sort(reverse=True)
            _best_score, _neg_pri, summary_source, summary, _flags = scored[0]
        else:
            # All candidates failed the semantic gate; keep source-priority order
            # so later pipeline fallback (prior-good / title) still has a sample.
            _pri, summary_source, summary = ranked[0]

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
    """Per-page enrich transport evidence (validators + outcome)."""

    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None
    ok: bool = True
    error_kind: str | None = None
    error_message: str | None = None
    raw_sha256: str | None = None
    decoded_sha256: str | None = None
    raw_bytes_received: int | None = None
    decoded_bytes_received: int | None = None
    selected_encoding: str | None = None


@dataclass(frozen=True, slots=True)
class _PageGet:
    """Internal page GET outcome (body optional on 304)."""

    html: str | None
    not_modified: bool
    etag: str | None = None
    last_modified: str | None = None
    status_code: int | None = None
    raw_sha256: str | None = None
    decoded_sha256: str | None = None
    raw_bytes_received: int | None = None
    decoded_bytes_received: int | None = None
    selected_encoding: str | None = None


def parse_page_metadata(html: str, *, page_url: str) -> dict[str, str | None]:
    """Extract short summary fields plus allowlisted image/keywords.

    Kept as a dict adapter for enrich callers and characterization tests.
    Quality score/flags live on ``PageMetadata`` / ``Essay`` (``_parse_page``).
    """
    parsed = _parse_page(html, page_url=page_url)
    page = parsed.metadata
    return {
        "page_title": page.page_title,
        "summary": page.summary,
        "summary_source": page.summary_source,
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
    prior_body_hash: str | None = None,
    host_cooldown: HostCooldown | None = None,
) -> _PageGet:
    """GET essay page with hop-safe redirects and optional conditional validators."""
    if host_cooldown is not None:
        host = urlsplit(url).hostname or ""
        host_cooldown.wait(host)
    cond = conditional_headers(etag=etag, last_modified=last_modified) or None
    result = get_with_evidence(
        client,
        url,
        allowed_hosts=ALLOWED_HOSTS,
        max_bytes=max_bytes,
        headers=cond,
        prior_etag=etag,
        prior_last_modified=last_modified,
        prior_body_hash=prior_body_hash,
    )
    ev = result.evidence
    if ev.result_kind is ResultKind.NOT_MODIFIED:
        return _PageGet(
            html=None,
            not_modified=True,
            etag=ev.etag or etag,
            last_modified=ev.last_modified or last_modified,
            status_code=304,
            raw_sha256=ev.raw_sha256,
            decoded_sha256=ev.decoded_sha256,
            raw_bytes_received=ev.bytes_received,
            decoded_bytes_received=ev.decoded_bytes_received,
            selected_encoding=None,
        )
    if ev.result_kind is ResultKind.FAILED:
        if result.response is None:
            raise httpx.TransportError(ev.error_message or f"Fetch failed for {url}")
        if ev.status_code == 304:
            raise FeedError(ev.error_message or "Unacceptable HTTP 304")
        result.response.raise_for_status()
        raise FeedError(ev.error_message or f"HTTP {ev.status_code}")
    if result.response is None:
        raise httpx.TransportError(ev.error_message or f"Fetch failed for {url}")
    result.response.raise_for_status()
    if ev.status_code != 200:
        raise FeedError(f"HTTP {ev.status_code} is not a usable essay page")
    if not result.body:
        raise FeedError(f"Empty HTTP 200 body for {url}")
    document = decode_html_document(result.body, transport_charset=ev.charset)
    return _PageGet(
        html=document.text,
        not_modified=False,
        etag=ev.etag,
        last_modified=ev.last_modified,
        status_code=ev.status_code,
        raw_sha256=ev.raw_sha256,
        decoded_sha256=ev.decoded_sha256,
        raw_bytes_received=ev.bytes_received,
        decoded_bytes_received=ev.decoded_bytes_received,
        selected_encoding=document.encoding,
    )


def _enrich_one(
    client: httpx.Client,
    essay: Essay,
    *,
    max_bytes: int,
    attempts: int,
    etag: str | None = None,
    last_modified: str | None = None,
    host_cooldown: HostCooldown | None = None,
) -> tuple[Essay, PageEnrichEvidence]:
    """Scrape one page; soft-fail to the original essay on network/parse errors.

    Always returns evidence so callers can distinguish success from failure
    without advancing a success TTL on soft-fail.
    """

    def _load() -> _PageGet:
        return _fetch_page(
            client,
            essay.url,
            max_bytes=max_bytes,
            etag=etag,
            last_modified=last_modified,
            prior_body_hash=essay.content_hash,
            host_cooldown=host_cooldown,
        )

    try:
        fetched = run_with_retry(_load, attempts=attempts, what=f"enrich {essay.url}")
    except FeedError as exc:
        logger.warning("Enrichment failed for {}: {}", essay.url, exc)
        logger.warning("{} {} | enrich_fetch | {}", REACHABILITY_FAIL_TOKEN, essay.url, exc)
        kind = type(exc).__name__
        return essay, PageEnrichEvidence(
            ok=False,
            error_kind=kind,
            error_message=str(exc)[:500],
        )

    if fetched.not_modified:
        return essay, PageEnrichEvidence(
            not_modified=True,
            etag=fetched.etag,
            last_modified=fetched.last_modified,
            status_code=304,
            ok=True,
            raw_sha256=fetched.raw_sha256,
            decoded_sha256=fetched.decoded_sha256,
            raw_bytes_received=fetched.raw_bytes_received,
            decoded_bytes_received=fetched.decoded_bytes_received,
            selected_encoding=fetched.selected_encoding,
        )

    assert fetched.html is not None
    try:
        page_hash = content_sha256(fetched.html)
        parsed = _parse_page(fetched.html, page_url=essay.url)
    except Exception as exc:
        logger.warning("Enrichment parse failed for {}: {}", essay.url, exc)
        logger.warning("{} {} | parse | {}", ENRICH_DEGRADED_TOKEN, essay.url, exc)
        return essay, PageEnrichEvidence(
            etag=fetched.etag,
            last_modified=fetched.last_modified,
            status_code=fetched.status_code,
            ok=False,
            error_kind="parse",
            error_message=str(exc)[:500],
            raw_sha256=fetched.raw_sha256,
            decoded_sha256=fetched.decoded_sha256,
            raw_bytes_received=fetched.raw_bytes_received,
            decoded_bytes_received=fetched.decoded_bytes_received,
            selected_encoding=fetched.selected_encoding,
        )

    page = parsed.metadata
    updated = essay.model_copy(
        update={
            "page_title": page.page_title,
            "summary": page.summary,
            "summary_source": page.summary_source,
            "quality_score": page.quality_score,
            "quality_flags": page.quality_flags,
            "content_text": None,
            "image_url": parsed.image_url,
            "keywords": parsed.keywords,
            "canonical_url": page.canonical_url or essay.url,
            "published_hint": page.published_hint,
            "published_at": None,
            "content_hash": page_hash,
        }
    )
    return updated, PageEnrichEvidence(
        not_modified=False,
        etag=fetched.etag,
        last_modified=fetched.last_modified,
        status_code=fetched.status_code,
        ok=True,
        raw_sha256=fetched.raw_sha256,
        decoded_sha256=fetched.decoded_sha256 or page_hash,
        raw_bytes_received=fetched.raw_bytes_received,
        decoded_bytes_received=fetched.decoded_bytes_received,
        selected_encoding=fetched.selected_encoding,
    )


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
    host_cooldown_seconds: float = 0.0,
    host_cooldown: HostCooldown | None = None,
) -> list[Essay]:
    """Fetch each essay page and attach short summaries (order preserved)."""
    if not essays:
        return essays
    attempts = max(1, retries + 1)
    validators = page_validators or {}
    cooldown = host_cooldown if host_cooldown is not None else HostCooldown(host_cooldown_seconds)
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
                host_cooldown=cooldown,
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
                logger.warning(
                    "{} {} | enrich_fetch | {}",
                    REACHABILITY_FAIL_TOKEN,
                    prior.url,
                    exc,
                )
                out[pos] = prior
                if page_evidence_out is not None:
                    page_evidence_out[prior.stable_id] = PageEnrichEvidence(
                        ok=False,
                        error_kind=type(exc).__name__,
                        error_message=str(exc)[:500],
                    )
                continue
            out[pos] = essay
            if page_evidence_out is not None:
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


def _probe_once(
    client: httpx.Client,
    essay: Essay,
    *,
    max_bytes: int,
    host_cooldown: HostCooldown | None = None,
) -> None:
    """Single probe attempt; raises httpx errors for tenacity."""
    if host_cooldown is not None:
        host = urlsplit(essay.url).hostname or ""
        host_cooldown.wait(host)
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
    host_cooldown: HostCooldown | None = None,
) -> str | None:
    """Return error message or None if OK (retries transient failures)."""
    try:
        run_with_retry(
            lambda: _probe_once(client, essay, max_bytes=max_bytes, host_cooldown=host_cooldown),
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
    host_cooldown_seconds: float = 0.0,
    host_cooldown: HostCooldown | None = None,
) -> LinkProbeReport:
    """Live-probe each essay URL; report failures without raising or dropping items."""
    if not essays:
        return LinkProbeReport(checked=0, ok=0, failures=())

    errors: list[str] = []
    attempts = max(1, retries + 1)
    cooldown = host_cooldown if host_cooldown is not None else HostCooldown(host_cooldown_seconds)
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
                host_cooldown=cooldown,
            ): e
            for e in essays
        }
        for fut in progress.track(
            as_completed(futures),
            total=len(futures),
            desc="Probe links",
            unit="url",
        ):
            essay = futures[fut]
            err = fut.result()
            if err:
                errors.append(err)
                logger.warning(
                    "{} {} | probe | {}",
                    REACHABILITY_FAIL_TOKEN,
                    essay.url,
                    err,
                )
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
