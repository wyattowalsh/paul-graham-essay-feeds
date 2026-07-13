"""JSON Feed 1.1 pure renderer."""

from __future__ import annotations

import json

from paul_graham_essay_feeds.domain import (
    JSON_FEED_VERSION,
    BuildContext,
    content_text_for,
)

__all__ = ["render_json_feed"]


def render_json_feed(context: BuildContext) -> bytes:
    """Render a JSON Feed 1.1 document.

    Omits ``date_published`` and ``date_modified``. Uses plural ``authors``.
    Includes ``feed_url`` only when public URLs are configured.
    """
    payload: dict = {
        "version": JSON_FEED_VERSION,
        "title": context.feed_title,
        "home_page_url": context.home_page_url,
        "description": context.feed_description,
        "language": context.language,
        "authors": [
            {
                "name": context.author_name,
                "url": context.author_url,
            }
        ],
        "items": [
            {
                "id": item.stable_id,
                "url": item.url,
                "title": item.title,
                "content_text": content_text_for(item),
            }
            for item in context.items
        ],
    }
    if context.public is not None:
        payload["feed_url"] = context.public.json_feed

    # Preserve a stable top-level key order for readability.
    ordered = {
        "version": payload["version"],
        "title": payload["title"],
        "home_page_url": payload["home_page_url"],
    }
    if "feed_url" in payload:
        ordered["feed_url"] = payload["feed_url"]
    ordered["description"] = payload["description"]
    ordered["language"] = payload["language"]
    ordered["authors"] = payload["authors"]
    ordered["items"] = payload["items"]

    return (json.dumps(ordered, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
