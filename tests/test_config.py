from pathlib import Path
import json
import tempfile
import unittest
from zoneinfo import ZoneInfo
from datetime import date
from nyaanews.config import Config
from nyaanews.util import window, normalize_url, parse_date

class ConfigTests(unittest.TestCase):
    def test_window_is_previous_natural_day(self):
        start, end = window(date(2026, 10, 25), ZoneInfo("Asia/Shanghai"))
        self.assertEqual(start.isoformat(), "2026-10-24T16:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-10-25T16:00:00+00:00")
        self.assertIsNone(parse_date("2026-10-25"))

    def test_identity_keeps_meaningful_query(self):
        self.assertEqual(normalize_url("https://EXAMPLE.invalid/a?id=3&utm_source=x#top"),
                         "https://example.invalid/a?id=3")
        self.assertNotEqual(normalize_url("https://example.invalid/a?id=4"),
                            normalize_url("https://example.invalid/a?id=3"))

    def test_unknown_topic_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/"runtime.env").write_text("DATA_DIR='" + td.replace("\\", "/") + "'\n")
            (root/"topics.json").write_text(json.dumps({"topics":[{"id":"sample"}]}))
            (root/"sources.json").write_text(json.dumps({"sources":[
                {"id":"feed","topics":["missing"],"feed_url":"https://example.invalid/feed"}]}))
            with self.assertRaises(ValueError):
                Config.load(root/"runtime.env")
