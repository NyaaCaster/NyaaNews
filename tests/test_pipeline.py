from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo
import tempfile
import unittest
from nyaanews.config import Config
from nyaanews.pipeline import archive_run
from nyaanews.network import Response
from nyaanews.network import FetchError
from nyaanews.extract import extract_article
from test_feeds import RSS
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

BODY = "An original calibration report describes seven independent instruments. " * 25
HTML = ('<html lang="en"><head><meta property="article:published_time" content="2026-10-25T10:00:00Z"></head>'
        '<body><article><h1>Calibration report</h1><p>' + BODY + '</p></article></body></html>').encode()

class PipelineTests(unittest.TestCase):
    def test_source_profile_and_http_failure_are_recorded(self):
        class HTTP:
            def get(inner,url,headers=None,*,profile='standard'):
                if url.endswith('feed'):
                    return Response(url,200,{},RSS)
                self.assertEqual(profile,'chrome')
                raise FetchError('browser_challenge',403)
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            cfg=Config(data/'runtime.env',{},[{'id':'sample'}],
                [{'id':'feed','feed_url':'https://example.invalid/feed','topics':['sample'],
                  'article_http_profile':'chrome'}],data,ZoneInfo('Asia/Shanghai'))
            report=archive_run(cfg,date(2026,10,25),http=HTTP(),run_sync=False)
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['errors'][0]['reason'],'browser_challenge')
            self.assertEqual(report['errors'][0]['http_status'],403)
            self.assertEqual(report['topics']['sample']['status'],'deferred')

    def test_three_days_eleven_topics_and_repeated_runs(self):
        class HTTP:
            day=date(2026,10,23)
            def get(self,url,headers=None):
                if url.endswith('/feed'):
                    topic=url.split('/')[-2]
                    stamp=format_datetime(datetime.combine(self.day,datetime.min.time(),timezone.utc))
                    entries=''.join(f'<item><title>Report {n}</title><link>https://example.invalid/{topic}/{self.day}/{n}</link><guid>{self.day}-{topic}-{n}</guid><pubDate>{stamp}</pubDate></item>' for n in range(4))
                    return Response(url,200,{},f'<rss version="2.0"><channel><title>Sample</title>{entries}</channel></rss>'.encode())
                page=HTML.replace(b'2026-10-25',str(self.day).encode()).replace(BODY.encode(),(url+' calibration data. ').encode()*50)
                return Response(url,200,{},page)
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            topics=[{"id":f"topic{n}","max_articles_per_day":3} for n in range(11)]
            sources=[{"id":t['id'],"feed_url":f"https://example.invalid/{t['id']}/feed","topics":[t['id']]} for t in topics]
            cfg=Config(data/'runtime.env',{},topics,sources,data,ZoneInfo('UTC'))
            http=HTTP()
            for offset in range(3):
                http.day=date(2026,10,23)+timedelta(days=offset)
                report=archive_run(cfg,http.day,http=http,run_sync=False)
                self.assertEqual(report['saved'],33)
                self.assertTrue(all(t['count']==3 for t in report['topics'].values()))
                self.assertEqual(archive_run(cfg,http.day,http=http,run_sync=False)['saved'],0)
            self.assertEqual(len(list(data.glob('news/*/*/*/*/*.md'))),99)

    def test_site_logo_is_not_article_title(self):
        page=HTML.replace(b'<body>',b'<body><h1>Site logo</h1>')
        c={"title":"Feed title","url":"https://example.invalid/a","feed_seen_at":"now"}
        a=extract_article(Response(c["url"],200,{},page),c,
                          {"id":"sample","feed_url":"https://example.invalid/feed"})
        self.assertEqual(a["title"],"Calibration report")

    def test_rerun_archive_cap_and_source_isolation(self):
        class HTTP:
            def get(self, url, headers=None):
                if url.endswith("feed"):
                    return Response(url,200,{},RSS)
                return Response(url,200,{"content-type":"text/html"},HTML)
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            config=Config(data/"runtime.env",{},[{"id":"sample","max_articles_per_day":1}],
                          [{"id":"feed","feed_url":"https://example.invalid/feed","topics":["sample"]}],
                          data,ZoneInfo("Asia/Shanghai"))
            first=archive_run(config,date(2026,10,25),http=HTTP(),run_sync=False)
            second=archive_run(config,date(2026,10,25),http=HTTP(),run_sync=False)
            self.assertEqual(first["saved"],1)
            self.assertEqual(second["saved"],0)
            self.assertEqual(second["topics"]["sample"]["count"],1)
            self.assertEqual(len(list(data.glob("news/*/*/*/*/*.md"))),1)
            self.assertTrue(list(data.glob("backups/*.db")))

    def test_submission_date_does_not_replace_unknown_page_date(self):
        page=HTML.replace(b'2026-10-25T10:00:00Z',b'2026-10-25')
        c={"title":"Synthetic","url":"https://example.invalid/a","published_at":"2026-10-25T10:00:00Z",
           "published_at_raw":"2026-10-25T10:00:00Z","feed_seen_at":"now"}
        with self.assertRaisesRegex(ValueError,"unverified_original"):
            extract_article(Response(c["url"],200,{},page), c,
                            {"id":"sample","feed_url":"https://example.invalid/feed","community":True})
