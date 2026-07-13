"""OPML 2.0 subscription catalog pure renderer."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from paul_graham_essay_feeds.domain import BuildContext, FeedError, rfc822_utc

__all__ = ["render_opml"]


def render_opml(context: BuildContext) -> bytes:
    """Render an OPML 2.0 subscription catalog for the generated feeds.

    Requires real public URLs. Never emits placeholders.

    Raises
    ------
    FeedError
        When ``context.public`` is ``None``.
    """
    if context.public is None:
        raise FeedError("OPML catalog generation requires a configured public base URL.")

    root = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = f"{context.feed_title} — Subscriptions"
    ET.SubElement(head, "dateCreated").text = rfc822_utc(context.build_updated_at)
    ET.SubElement(head, "dateModified").text = rfc822_utc(context.build_updated_at)
    ET.SubElement(head, "ownerName").text = context.author_name

    body = ET.SubElement(root, "body")
    ET.SubElement(
        body,
        "outline",
        {
            "text": context.feed_title,
            "title": context.feed_title,
            "type": "rss",
            "xmlUrl": context.public.rss,
            "htmlUrl": context.home_page_url,
        },
    )
    ET.SubElement(
        body,
        "outline",
        {
            "text": f"{context.feed_title} (Atom)",
            "title": f"{context.feed_title} (Atom)",
            "type": "rss",
            "xmlUrl": context.public.atom,
            "htmlUrl": context.home_page_url,
        },
    )
    ET.SubElement(
        body,
        "outline",
        {
            "text": f"{context.feed_title} (JSON Feed)",
            "title": f"{context.feed_title} (JSON Feed)",
            "type": "link",
            "url": context.public.json_feed,
        },
    )

    ET.indent(root, space="  ")
    xml_body = ET.tostring(
        root,
        encoding="utf-8",
        xml_declaration=False,
        short_empty_elements=True,
    )
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + b"\n"
