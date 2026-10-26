from pathlib import Path
import json
import tempfile
import unittest
from nyaanews.store import Store
from nyaanews.config import Config
from nyaanews.sync import sync_pending, chunks_for
from nyaanews.util import digest
from nyaanews.extract import markdown
from zoneinfo import ZoneInfo

class SyncTests(unittest.TestCase):
    def prepare(self, folder):
        data=Path(folder)
        cfg=Config(data/"runtime.env",{"ASTRBOT_KB_NAME":"Synthetic KB"},[],[],data,ZoneInfo("UTC"))
        cfg.prepare()
        store=Store(data)
        body=("Original paragraph. "*80).strip()
        meta={"article_id":"sample","title":"Synthetic","published_at":"2026-10-25T00:00:00Z",
              "fetched_at":"2026-10-26T00:00:00Z",
              "canonical_url":"https://example.invalid/a","content_sha256":digest(body),
              "target_date":"2026-10-25","primary_topic":"sample"}
        (data/"news/a.md").write_text(markdown(dict(meta,body=body)),encoding="utf-8")
        store.register(meta,"news/a.md",3)
        return cfg,store

    def test_unknown_post_not_replayed_on_next_sync(self):
        class Gateway:
            posts=0
            def call(self, method,path,body=None):
                if path.startswith("/api/v1/knowledge-bases?"):
                    return {"items":[{"kb_id":"kb","kb_name":"Synthetic KB"}],"total":1}
                if method=="POST":
                    self.posts+=1
                    raise RuntimeError("Connection lost after acceptance")
                return {"items":[],"total":0}
        with tempfile.TemporaryDirectory() as td:
            cfg,store=self.prepare(td)
            try:
                gateway=Gateway()
                self.assertEqual(sync_pending(cfg,store,gateway)["unknown"],1)
                self.assertEqual(sync_pending(cfg,store,gateway)["unknown"],1)
                self.assertEqual(gateway.posts,1)
            finally:store.close()

    def test_chunks_preserve_all_body(self):
        meta={"title":"Synthetic","published_at":"2026-10-25","canonical_url":"https://example.invalid/a"}
        body="abcde"*1000
        chunks=chunks_for(meta,"\n## Article\n\n"+body,300)
        header=f'{meta["title"]}\n{meta["published_at"]}\n{meta["canonical_url"]}\n'
        self.assertEqual("".join(c[len(header):].strip() for c in chunks),body)
        self.assertTrue(all(len(c)<=300 for c in chunks))

    def test_accepted_request_reconciles_without_second_post(self):
        class Gateway:
            posts=0
            accepted=False
            def call(self,method,path,body=None):
                if path.startswith('/api/v1/knowledge-bases?'):
                    return {'items':[{'kb_id':'kb','kb_name':'Synthetic KB'}],'total':1}
                if method=='POST':
                    self.posts+=1
                    self.doc=body['documents'][0]
                    self.accepted=True
                    raise RuntimeError('Lost acknowledgement')
                if '/chunks?' in path:
                    return {'items':[{'text':text} for text in self.doc['chunks']],'total':len(self.doc['chunks'])}
                if '/documents?' in path:
                    items=[{'doc_id':'doc','doc_name':self.doc['file_name']}] if self.accepted else []
                    return {'items':items,'total':len(items)}
                return {'doc_id':'doc'}
        with tempfile.TemporaryDirectory() as td:
            cfg,store=self.prepare(td)
            try:
                gateway=Gateway()
                self.assertEqual(sync_pending(cfg,store,gateway)['unknown'],1)
                self.assertEqual(sync_pending(cfg,store,gateway)['verified'],1)
                self.assertEqual(gateway.posts,1)
            finally:store.close()
