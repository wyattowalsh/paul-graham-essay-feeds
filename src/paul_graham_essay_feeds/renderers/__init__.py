"""Pure feed renderers: canonical models in, bytes out."""

from __future__ import annotations

from typing import Protocol

from paul_graham_essay_feeds.domain import BuildContext
from paul_graham_essay_feeds.renderers.atom import render_atom
from paul_graham_essay_feeds.renderers.json_feed import render_json_feed
from paul_graham_essay_feeds.renderers.opml import render_opml
from paul_graham_essay_feeds.renderers.rss import render_rss

__all__ = [
    "Renderer",
    "render_atom",
    "render_json_feed",
    "render_opml",
    "render_rss",
]


class Renderer(Protocol):
    """Protocol for pure deterministic feed serializers."""

    def __call__(self, context: BuildContext) -> bytes:
        """Render ``context`` to UTF-8 document bytes."""
        ...
