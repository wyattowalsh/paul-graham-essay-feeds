"""Unit tests for models.py (Essay, constants, helpers)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from paul_graham_essay_feeds import __version__
from paul_graham_essay_feeds.models import (
    FEED_SUMMARY_CHARS,
    GENERATOR,
    Essay,
    FeedError,
    blurb,
    canonicalize_url,
    content_sha256,
    is_content_candidate,
    make_stable_id,
    normalize_text,
    rfc822,
    rfc3339,
    truncate_text,
    user_agent,
    utc_now,
    validate_essay_link,
)


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  a\n\tb  ") == "a b"
    assert normalize_text("x\x00y") == "xy"


def test_canonicalize_essay_url() -> None:
    url = canonicalize_url("https://paulgraham.com/articles.html", "foo.html")
    assert url == "https://paulgraham.com/foo.html"


def test_canonicalize_www_host() -> None:
    url = canonicalize_url("https://www.paulgraham.com/", "https://www.paulgraham.com/z.html")
    assert url == "https://paulgraham.com/z.html"


def test_canonicalize_strips_turbify_query() -> None:
    url = canonicalize_url(
        "https://paulgraham.com/articles.html",
        "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=999",
    )
    assert url == "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt"


def test_canonicalize_rejects_bad_host() -> None:
    with pytest.raises(FeedError, match="not allowed"):
        canonicalize_url("https://paulgraham.com/", "https://evil.example/x.html")


def test_canonicalize_rejects_non_https() -> None:
    with pytest.raises(FeedError, match="Bad URL"):
        canonicalize_url("https://paulgraham.com/", "http://paulgraham.com/x.html")


def test_is_content_candidate_excludes_index() -> None:
    assert not is_content_candidate("https://paulgraham.com/articles.html", "Essays")
    assert not is_content_candidate("https://paulgraham.com/foo.html", "")
    assert is_content_candidate("https://paulgraham.com/foo.html", "Foo")


def test_stable_id_permalink_vs_turbify() -> None:
    sid, perm = make_stable_id("https://paulgraham.com/a.html")
    assert sid == "https://paulgraham.com/a.html"
    assert perm is True
    sid2, perm2 = make_stable_id("https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=1")
    assert sid2.startswith("urn:uuid:")
    assert perm2 is False


def test_blurb_and_timestamps() -> None:
    assert "Hello" in blurb("Hello")
    now = utc_now()
    assert "GMT" in rfc822(now) or "UTC" in rfc822(now)
    assert rfc3339(now).endswith("Z")


def test_essay_pydantic_frozen() -> None:
    e = Essay(
        position=1,
        title="T",
        url="https://paulgraham.com/t.html",
        stable_id="https://paulgraham.com/t.html",
        is_permalink=True,
    )
    assert e.model_config.get("frozen") is True


def test_essay_rejects_bad_host() -> None:
    with pytest.raises(ValidationError):
        Essay(
            position=1,
            title="X",
            url="https://evil.example/x.html",
            stable_id="x",
            is_permalink=True,
        )


def test_feed_summary_helpers() -> None:
    bare = Essay(
        position=1,
        title="T",
        url="https://paulgraham.com/t.html",
        stable_id="https://paulgraham.com/t.html",
        is_permalink=True,
    )
    assert "T" in bare.feed_summary()
    rich = bare.model_copy(update={"summary": "Short"})
    assert rich.feed_summary() == "Short"
    mid = bare.model_copy(update={"content_text": "x" * 900})
    assert mid.feed_summary().endswith("…")
    # RV-S-001: even a long summary field is capped at FEED_SUMMARY_CHARS.
    long_sum = bare.model_copy(update={"summary": "S" * 2000})
    out = long_sum.feed_summary()
    assert out.endswith("…")
    assert len(out) <= FEED_SUMMARY_CHARS


def test_truncate_text_word_boundary() -> None:
    assert truncate_text("short") == "short"
    long = "word " * 100
    out = truncate_text(long, 40)
    assert out.endswith("…")
    assert len(out) <= 40
    # Space-less overflow must still fit verify's [1, max_chars] cap.
    nospace = truncate_text("S" * 80, 40)
    assert nospace.endswith("…")
    assert len(nospace) == 40


def test_content_sha256_stable() -> None:
    assert content_sha256("abc") == content_sha256(b"abc")
    assert content_sha256("a") != content_sha256("b")
    assert len(content_sha256("x")) == 64


def test_essay_index_fingerprint() -> None:
    e = Essay(
        position=1,
        title="T",
        url="https://paulgraham.com/t.html",
        stable_id="https://paulgraham.com/t.html",
        is_permalink=True,
    )
    assert (
        e.index_fingerprint()
        == "1\thttps://paulgraham.com/t.html\thttps://paulgraham.com/t.html\tT"
    )


def test_essay_fields_have_descriptions() -> None:
    """All public model fields carry Field descriptions (annotation contract)."""
    for name, field in Essay.model_fields.items():
        assert field.description, f"Essay.{name} missing description"


def test_generator_and_user_agent_from_version() -> None:
    assert __version__ in GENERATOR
    assert f"pg-essay-feeds/{__version__}" == GENERATOR
    assert __version__ in user_agent()
    assert user_agent(" link-check").endswith(" link-check")
    assert __version__ in user_agent(" link-check")


def test_validate_essay_link_ok() -> None:
    e = Essay(
        position=1,
        title="T",
        url="https://paulgraham.com/t.html",
        stable_id="https://paulgraham.com/t.html",
        is_permalink=True,
    )
    validate_essay_link(e)


def test_validate_essay_link_rejects_fragment() -> None:
    bad = Essay.model_construct(
        position=1,
        title="T",
        url="https://paulgraham.com/t.html#frag",
        stable_id="x",
        is_permalink=True,
    )
    with pytest.raises(FeedError, match="Fragment"):
        validate_essay_link(bad)
