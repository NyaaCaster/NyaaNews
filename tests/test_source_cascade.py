from datetime import date
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo
from unittest.mock import patch
from nyaanews.config import Config
from nyaanews.network import Response, FetchError
from nyaanews.pipeline import archive_run, relevant
from test_pipeline import HTML, BODY

class CascadeHTTP:
    def __init__(self, entries, failed=(), bad_articles=()):
        self.entries=entries
        self.failed=set(failed)
        self.bad_articles=set(bad_articles)
        self.calls=[]

    def get(self,url,headers=None,**kwargs):
        self.calls.append(url)
        if url.endswith('/feed'):
            sid=url.split('/')[-2]
            if sid in self.failed:
                raise FetchError('http_error',503)
            items=''.join(f'<item><title>{title}</title><link>https://example.invalid/{aid}</link><guid>{sid}-{aid}</guid><pubDate>Sun, 25 Oct 2026 10:00:00 GMT</pubDate></item>'
                          for aid,title in self.entries.get(sid,[]))
            return Response(url,200,{},f'<rss version="2.0"><channel><title>Sample</title>{items}</channel></rss>'.encode())
        if url.rsplit('/',1)[1] in self.bad_articles:
            raise FetchError('http_error',403)
        body=(url+' independent measured findings. ')*40
        page=HTML.replace(BODY.encode(),body.encode())
        return Response(url,200,{},page)

class SourceCascadeTests(unittest.TestCase):
    def test_title_rules_do_not_match_unrelated_summary_keywords(self):
        source={'topic_rules':{'sample':{'title_include':['architecture|protocol'],
                                        'title_exclude':['controller']}}}
        self.assertFalse(relevant(source,{'title':'New controller support','summary':'kernel architecture','url':'https://example.invalid/a'},'sample'))
        self.assertTrue(relevant(source,{'title':'New remote protocol','summary':'','url':'https://example.invalid/a'},'sample'))

    def config(self,data,topics,sources):
        return Config(data/'runtime.env',{},topics,sources,data,ZoneInfo('UTC'))

    def source(self,sid,topics):
        return {'id':sid,'topics':topics,'feed_url':f'https://example.invalid/{sid}/feed'}

    def test_priority_fallback_stops_feed_and_article_requests_at_cap(self):
        topics=[{'id':'sample','source_order':['first','second','third'],'max_articles_per_day':3}]
        sources=[self.source(s,['sample']) for s in ('third','second','first')]
        http=CascadeHTTP({'first':[('one','A')],
                          'second':[('one','Duplicate'),('two','B'),('three','C'),('four','Surplus')],
                          'third':[('five','Unused')]})
        with tempfile.TemporaryDirectory() as td:
            cfg=self.config(Path(td),topics,sources)
            first=archive_run(cfg,date(2026,10,25),http=http,run_sync=False)
            self.assertEqual(first['saved'],3)
            self.assertEqual(first['topics']['sample']['sources_used'],['first','second'])
            self.assertEqual(first['topics']['sample']['stop_reason'],'quota_met')
            self.assertNotIn('https://example.invalid/third/feed',http.calls)
            self.assertNotIn('https://example.invalid/four',http.calls)
            self.assertEqual(http.calls.count('https://example.invalid/one'),1)
            http.calls.clear()
            second=archive_run(cfg,date(2026,10,25),http=http,run_sync=False)
            self.assertEqual(second['saved'],0)
            self.assertEqual(http.calls,[])

    def test_all_sources_exhausted_preserves_shortfall_and_pending_documents(self):
        topics=[{'id':'sample','source_order':['first','second','third']}]
        sources=[self.source(s,['sample']) for s in ('first','second','third')]
        http=CascadeHTTP({'first':[],'second':[('one','A')],'third':[]})
        with tempfile.TemporaryDirectory() as td:
            cfg=self.config(Path(td),topics,sources)
            cfg.values['SYNC_ENABLED']='1'
            with patch('nyaanews.sync.sync_pending',return_value={'verified':1,'pending':0,'unknown':0,'failed':0}) as sync:
                report=archive_run(cfg,date(2026,10,25),http=http)
                sync.assert_called_once()
            self.assertEqual(report['sync']['verified'],1)
            self.assertEqual(report['status'],'completed')
            self.assertEqual(report['remaining_count'],2)
            self.assertEqual(report['topics']['sample']['stop_reason'],'sources_exhausted')
            self.assertEqual(len(report['sources']),3)
            import sqlite3
            with closing(sqlite3.connect(cfg.data/'state/nyaanews.db')) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0],1)

    def test_failed_source_and_article_continue_to_later_source(self):
        topics=[{'id':'sample','source_order':['first','second','third'],'max_articles_per_day':1}]
        sources=[self.source(s,['sample']) for s in ('first','second','third')]
        http=CascadeHTTP({'second':[('bad','Fail')],'third':[('good','Success')]},failed=['first'],bad_articles=['bad'])
        with tempfile.TemporaryDirectory() as td:
            report=archive_run(self.config(Path(td),topics,sources),date(2026,10,25),http=http,run_sync=False)
            self.assertEqual(report['saved'],1)
            self.assertEqual(report['remaining_count'],0)
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['errors'][0]['http_status'],403)

    def test_shared_feed_once_with_topic_rules_and_cross_topic_dedup(self):
        topics=[{'id':'alpha','source_order':['shared'],'max_articles_per_day':1},
                {'id':'beta','source_order':['shared','backup'],'max_articles_per_day':1}]
        shared=self.source('shared',['alpha','beta'])
        shared['topic_rules']={'alpha':{'include':['Alpha']},'beta':{'include':['Beta']}}
        http=CascadeHTTP({'shared':[('one','Alpha Beta'),('two','Beta exclusive')],
                          'backup':[('three','Unused')]})
        with tempfile.TemporaryDirectory() as td:
            report=archive_run(self.config(Path(td),topics,[shared,self.source('backup',['beta'])]),
                               date(2026,10,25),http=http,run_sync=False)
            self.assertEqual(report['saved'],2)
            self.assertEqual(http.calls.count('https://example.invalid/shared/feed'),1)
            self.assertNotIn('https://example.invalid/backup/feed',http.calls)
            self.assertEqual(http.calls.count('https://example.invalid/one'),1)
