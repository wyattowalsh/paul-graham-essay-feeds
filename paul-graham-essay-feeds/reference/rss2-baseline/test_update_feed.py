from __future__ import annotations

import threading
import unittest
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import update_feed as subject


class ExtractorTests(unittest.TestCase):
    def test_marker_extraction_and_url_normalization(self) -> None:
        rows = "".join(
            f'<img src="https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif">'
            f'<a href="essay-{index}.html">Essay {index}</a>'
            for index in range(1, 234)
        )
        rows += (
            '<img src="https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif">'
            '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
            'acl1.txt?t=123&amp;">Chapter 1 of Ansi Common Lisp</a>'
            '<img src="https://s.turbifycdn.com/aah/paulgraham/the-reddits-2.gif">'
            '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
            'acl2.txt?t=123&amp;">Chapter 2 of Ansi Common Lisp</a>'
        )
        result = subject.extract_items(
            rows,
            base_url=subject.SOURCE_URL,
            min_items=233,
        )
        self.assertEqual(result.mode, "essay-row-marker")
        self.assertEqual(len(result.items), 235)
        self.assertEqual(
            result.items[-2].url,
            "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=123",
        )
        self.assertFalse(result.items[-2].guid_is_permalink)

    def test_fallback_keeps_last_duplicate_occurrence(self) -> None:
        recommendations = (
            '<a href="greatwork.html">How to Do Great Work</a>'
            '<a href="kids.html">Having Kids</a>'
            '<a href="selfindulgence.html">How to Lose Time and Money</a>'
        )
        main = recommendations
        for index in range(230):
            main += f'<a href="item-{index}.html">Item {index}</a>'
        main += (
            '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
            'acl1.txt?t=1">Chapter 1 of Ansi Common Lisp</a>'
            '<a href="https://sep.turbifycdn.com/ty/cdn/paulgraham/'
            'acl2.txt?t=1">Chapter 2 of Ansi Common Lisp</a>'
        )
        result = subject.extract_items(
            recommendations + main,
            base_url=subject.SOURCE_URL,
            min_items=233,
        )
        self.assertEqual(result.mode, "filtered-anchor-fallback")
        self.assertEqual(result.duplicate_count, 3)
        self.assertEqual(result.items[0].title, "How to Do Great Work")


class ReconciliationTests(unittest.TestCase):
    @staticmethod
    def _item(position: int, slug: str) -> subject.FeedItem:
        url = f"https://paulgraham.com/{slug}.html"
        guid, is_permalink = subject.make_guid(url)
        return subject.FeedItem(position, slug, url, guid, is_permalink)

    def test_new_prefix_is_accepted(self) -> None:
        old = (self._item(1, "a"), self._item(2, "b"))
        current = (
            self._item(1, "new"),
            self._item(2, "a"),
            self._item(3, "b"),
        )
        changes = subject.reconcile_items(
            old,
            current,
            allow_removals=False,
            allow_nonprefix_additions=False,
        )
        self.assertEqual(len(changes.added), 1)
        self.assertFalse(changes.removed)

    def test_nonprefix_addition_is_rejected(self) -> None:
        old = (self._item(1, "a"), self._item(2, "b"))
        current = (
            self._item(1, "a"),
            self._item(2, "new"),
            self._item(3, "b"),
        )
        with self.assertRaises(subject.FeedError):
            subject.reconcile_items(
                old,
                current,
                allow_removals=False,
                allow_nonprefix_additions=False,
            )

    def test_removal_is_rejected(self) -> None:
        old = (self._item(1, "a"), self._item(2, "b"))
        current = (self._item(1, "a"),)
        with self.assertRaises(subject.FeedError):
            subject.reconcile_items(
                old,
                current,
                allow_removals=False,
                allow_nonprefix_additions=False,
            )


class NormalizationTests(unittest.TestCase):
    def test_invalid_xml_controls_are_removed(self) -> None:
        self.assertEqual(subject.normalize_text("A\x01  B"), "A B")

    def test_legacy_double_prefix_is_rejected(self) -> None:
        malformed = (
            '<img src="https://s.turbifycdn.com/aah/paulgraham/'
            'the-reddits-2.gif">'
            '<a href="http://www.paulgraham.com/https://sep.turbifycdn.com/'
            'ty/cdn/paulgraham/acl1.txt">Bad</a>'
        )
        with self.assertRaises(subject.FeedError):
            subject.extract_items(
                malformed,
                base_url=subject.SOURCE_URL,
                min_items=1,
            )


class FetchTests(unittest.TestCase):
    def test_fetch_and_conditional_304(self) -> None:
        body = b"<html><body>ok</body></html>"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.headers.get("If-None-Match") == '"fixture-etag"':
                    self.send_response(304)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("ETag", '"fixture-etag"')
                self.send_header(
                    "Last-Modified",
                    "Sat, 11 Jul 2026 00:00:00 GMT",
                )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/articles.html"
        try:
            initial = subject.fetch_source(
                url,
                timeout=2.0,
                retries=0,
                max_bytes=1024,
                state={},
                conditional=False,
            )
            self.assertEqual(initial.status, 200)
            self.assertEqual(initial.body, body)
            self.assertEqual(initial.etag, '"fixture-etag"')

            cached = subject.fetch_source(
                url,
                timeout=2.0,
                retries=0,
                max_bytes=1024,
                state={
                    "etag": initial.etag,
                    "last_modified": initial.last_modified,
                },
                conditional=True,
            )
            self.assertTrue(cached.not_modified)
            self.assertEqual(cached.status, 304)
            self.assertIsNone(cached.body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)


class FeedTests(unittest.TestCase):
    def test_final_artifacts_validate(self) -> None:
        project = Path(__file__).resolve().parent
        manifest = subject.load_json(project / "paul-graham-essays.items.json")
        items = subject._manifest_items(manifest)
        report = subject.validate_feed_bytes(
            (project / "paul-graham-essays.rss.xml").read_bytes(),
            expected_items=items,
            min_items=233,
            expected_self_url=None,
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["item_count"], 233)
        self.assertEqual(report["unique_guid_count"], 233)

    def test_build_feed_has_required_rss_structure(self) -> None:
        internal_url = "https://paulgraham.com/example.html"
        internal_guid, internal_permalink = subject.make_guid(internal_url)
        external_url = "https://sep.turbifycdn.com/ty/cdn/paulgraham/acl1.txt?t=1"
        external_guid, external_permalink = subject.make_guid(external_url)
        items = (
            subject.FeedItem(
                1,
                "Example",
                internal_url,
                internal_guid,
                internal_permalink,
            ),
            subject.FeedItem(
                2,
                "Chapter 1 of Ansi Common Lisp",
                external_url,
                external_guid,
                external_permalink,
            ),
        )
        xml_bytes = subject.build_feed(
            items,
            last_build_date=datetime(2026, 1, 1, tzinfo=UTC),
            self_url="https://example.com/feed.xml",
        )
        root = ET.fromstring(xml_bytes)
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.attrib["version"], "2.0")
        self.assertEqual(len(root.findall("./channel")), 1)
        self.assertEqual(len(root.findall("./channel/item")), 2)
        self.assertEqual(
            len(root.findall(f"./channel/{{{subject.ATOM_NS}}}link")),
            1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
