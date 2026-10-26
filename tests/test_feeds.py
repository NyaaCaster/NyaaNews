from pathlib import Path
import tempfile
import unittest
from nyaanews.feeds import parse_feed, poll
from nyaanews.store import Store
from nyaanews.network import Response, public_addresses, FetchError
from unittest.mock import patch

RSS = b'<rss version="2.0"><channel><title>Example</title><item><title>A</title><link>https://example.invalid/a</link><guid>x</guid><pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>'
ATOM = b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Sample</title><entry><id>x</id><title>A</title><published>2026-10-25T12:00:00Z</published><link rel="alternate" type="text/html" href="https://example.invalid/a"/><link rel="replies" type="application/atom+xml" href="https://example.invalid/a/feed/"/></entry></feed>'

class FeedTests(unittest.TestCase):
    def test_atom_alternate_not_last_reply(self):
        self.assertEqual(parse_feed(ATOM, "https://example.invalid/feed", "sample")[0]["url"],
                         "https://example.invalid/a")

    def test_304_retains_candidate_cache(self):
        class HTTP:
            def __init__(self):
                self.count = 0
            def get(self, url, headers):
                self.count += 1
                if self.count == 1:
                    return Response(url, 200, {"etag":"version1"}, RSS)
                self.headers = headers
                return Response(url, 304, {}, b"")
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td))
            try:
                http = HTTP()
                source = {"id":"sample", "feed_url":"https://example.invalid/feed"}
                self.assertEqual(poll(source, store, http)["count"], 1)
                self.assertTrue(poll(source, store, http)["not_modified"])
                self.assertEqual(http.headers["If-None-Match"], "version1")
                self.assertEqual(len(store.candidates()), 1)
            finally:
                store.close()

    def test_private_or_mixed_dns_rejected(self):
        for addresses in (["127.0.0.1"], ["8.8.8.8", "10.0.0.1"], ["::ffff:127.0.0.1"]):
            with patch("socket.getaddrinfo", return_value=[(2,1,6,"",(a,443)) for a in addresses]):
                with self.assertRaises(FetchError):
                    public_addresses("example.invalid", 443)

    def test_http_429_backoff_does_not_erase_cache(self):
        class HTTP:
            def get(self, *_):
                raise FetchError("http_error",429,"1200")
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td))
            try:
                source = {"id":"sample","feed_url":"https://example.invalid"}
                result = poll(source, store, HTTP())
                self.assertEqual(result["http_status"],429)
                self.assertEqual(poll(source, store, HTTP())["status"],"deferred")
            finally:
                store.close()
