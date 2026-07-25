"""Content-root page metadata extraction and summary quality scoring.

Improves on :func:`paul_graham_essay_feeds.enrich.parse_page_metadata` by:

- Preferring clean meta / Open Graph descriptions over body chrome
- Skipping nav / footer / promo regions when taking a content paragraph
- Recording summary provenance + a quality score with explicit flags
- Month+year → ``published_hint`` only (never invents day-1 ``published_at``)
- Treating ``rel=canonical`` as a multi-token list (hint, not identity)
- Never retaining a full essay body (summary capped at ``FEED_SUMMARY_CHARS``)
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Final, Literal
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, field_validator

from paul_graham_essay_feeds.decoding import decode_html_document
from paul_graham_essay_feeds.model import FEED_SUMMARY_CHARS, normalize_text, truncate_text

# Month+year on the page is a human hint only (ADR-003); never invent day-1 dates.
_MONTH_YEAR = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(\d{4})\b",
    re.I,
)

_SKIP_TAGS: Final = frozenset({"script", "style", "noscript", "svg", "template"})
_CHROME_TAGS: Final = frozenset({"nav", "footer", "header", "aside", "form", "menu"})
# HTML void elements never emit end tags; do not open chrome depth for them.
_VOID_TAGS: Final = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
# class/id/role substrings that mark promo / chrome regions on essay pages.
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

# Minimum length for a content paragraph to be considered usable.
_MIN_PARAGRAPH_CHARS: Final = 40
# Soft floor for a "good" summary (below → too_short flag).
_MIN_GOOD_SUMMARY_CHARS: Final = 40
# Bound scan text used only for date hints / paragraph selection (never stored).
_MAX_SCAN_CHARS: Final = 2_000

SummarySource = Literal[
    "meta_description",
    "og_description",
    "twitter_description",
    "content_paragraph",
]


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


class _PageParser(HTMLParser):
    """Collect title, meta, canonical, and chrome-filtered content paragraphs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.canonical_href: str | None = None
        self.paragraphs: list[str] = []
        self._para_parts: list[str] = []
        self._in_p = 0
        self._skip = 0
        self._chrome = 0
        # Stack of tags that opened a chrome region (for correct depth on endtag).
        self._chrome_stack: list[str] = []
        self._loose_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        amap = {k.lower(): (v or "") for k, v in attrs}

        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return

        if tag == "title":
            self._in_title = True
            return

        if tag == "meta":
            name = (amap.get("name") or amap.get("property") or "").lower().strip()
            content = amap.get("content", "").strip()
            if name and content and name not in self.meta:
                self.meta[name] = content
            return

        if tag == "link":
            # rel is a space-separated token list (HTML living standard).
            rel_tokens = amap.get("rel", "").lower().split()
            if "canonical" in rel_tokens:
                href = amap.get("href", "").strip()
                if href and self.canonical_href is None:
                    self.canonical_href = href
            return

        is_chrome = tag in _CHROME_TAGS or _attrs_look_like_chrome(amap)
        if self._chrome:
            # Nested inside chrome: track nested chrome opens (non-void only).
            if is_chrome and tag not in _VOID_TAGS:
                self._chrome += 1
                self._chrome_stack.append(tag)
            return

        if is_chrome:
            if tag not in _VOID_TAGS:
                self._chrome += 1
                self._chrome_stack.append(tag)
            return

        if tag == "p":
            self._in_p += 1
            if self._in_p == 1:
                self._para_parts = []
            return

        if tag in {"br", "hr"} and self._in_p:
            self._para_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
            return
        if self._chrome_stack and self._chrome_stack[-1] == tag:
            self._chrome_stack.pop()
            if self._chrome:
                self._chrome -= 1
            return
        if tag == "p" and self._in_p:
            self._in_p -= 1
            if self._in_p == 0:
                text = normalize_text(" ".join(self._para_parts))
                if text:
                    self.paragraphs.append(text)
                self._para_parts = []
            return

    def handle_data(self, data: str) -> None:
        if self._skip or self._chrome:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
            return
        if self._in_p:
            self._para_parts.append(text)
            return
        # Loose body text (PG pages are often bare text without <p>).
        self._loose_parts.append(text)

    def loose_body(self) -> str:
        return normalize_text(" ".join(self._loose_parts))


def _attrs_look_like_chrome(amap: dict[str, str]) -> bool:
    role = amap.get("role", "").lower()
    if role in {"navigation", "banner", "contentinfo", "complementary"}:
        return True
    return any(_CHROME_ATTR.search(amap.get(key, "")) for key in ("id", "class", "aria-label"))


def _optional_norm(value: str | None) -> str | None:
    if not value:
        return None
    text = normalize_text(value)
    return text or None


def _resolve_canonical(href: str | None, *, page_url: str) -> str | None:
    """Resolve canonical href against page_url; return absolute string or None.

    Canonical is a **hint** only — callers must not rewrite catalog identity from it.
    """
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
    # Ultra-short nav crumbs.
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

    # Split loose body into sentence-ish chunks and skip leading promo.
    # PG pages often prefix "Want to start a startup? Get funded by Y Combinator."
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
            # Bound scan material; final summary cap applied later.
            if len(joined) > _MAX_SCAN_CHARS:
                return joined[:_MAX_SCAN_CHARS]
            return joined

    joined = " ".join(kept) if kept else body
    if not joined or _is_promo_or_chrome(joined):
        # If everything looks like promo, still return a short non-empty tail for scoring.
        cleaned = body
        for promo in _PROMO_LINE.finditer(body):
            # Drop leading promo match once.
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
    """Return ``(quality_score, quality_flags)`` for a candidate summary.

    Penalties (stacked, floored at 0.0):

    - empty → 0.0 + ``empty``
    - too short (< ``_MIN_GOOD_SUMMARY_CHARS``) → -0.35 + ``too_short``
    - contains ``Subscribe`` → -0.40 + ``subscribe``
    - contains ``Click here`` → -0.30 + ``click_here``
    - nav/promo-like text → -0.40 + ``nav_like``
    - U+FFFD replacement char → -0.50 + ``replacement_char``
    """
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
    # Stable flag order for deterministic artifacts.
    return score, tuple(flags)


def extract_page_metadata(html: str | bytes, *, page_url: str) -> PageMetadata:
    """Extract content-root metadata and a short quality-scored summary.

    Parameters
    ----------
    html:
        HTML document as ``str`` or raw ``bytes``. Bytes are decoded via
        :func:`decode_html_document` (ADR-004 priority chain).
    page_url:
        Absolute page URL used to resolve relative canonical links.

    Returns
    -------
    PageMetadata
        Never includes a full essay body; ``summary`` is ≤ ``FEED_SUMMARY_CHARS``.
        Month+year becomes ``published_hint`` only (no day-1 ``published_at``).
    """
    text = decode_html_document(html).text if isinstance(html, bytes) else html

    parser = _PageParser()
    parser.feed(text)
    parser.close()

    page_title = _optional_norm(" ".join(parser.title_parts))
    meta_description = _optional_norm(parser.meta.get("description"))
    og_title = _optional_norm(parser.meta.get("og:title"))
    og_description = _optional_norm(parser.meta.get("og:description"))
    twitter_description = _optional_norm(parser.meta.get("twitter:description"))

    canonical_url = _resolve_canonical(parser.canonical_href, page_url=page_url)

    loose = parser.loose_body()
    content_paragraph = _first_content_paragraph(
        parser.paragraphs,
        loose,
        page_title=page_title,
    )

    # Scan early paragraphs + loose body for month+year (hint only; no day-1 date).
    early_scan = " ".join(parser.paragraphs[:8] + ([loose] if loose else []))
    published_hint = _published_hint_from_text(
        early_scan,
        content_paragraph,
        meta_description,
        og_description,
    )

    # Preference: meta description → og:description → twitter → content paragraph.
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

    # Cap free-text metadata fields so no full-body-sized strings are retained.
    def _cap(value: str | None) -> str | None:
        if not value:
            return value
        return truncate_text(value, FEED_SUMMARY_CHARS) or None

    quality_score, quality_flags = score_summary_quality(summary)

    return PageMetadata(
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
