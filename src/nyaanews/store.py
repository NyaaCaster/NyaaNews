"""All mutations pass through a backed-up transaction; files are recoverable source records."""
from contextlib import closing, contextmanager
import json
import sqlite3
import uuid
from .util import now

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, day TEXT, started TEXT, ended TEXT, status TEXT, report TEXT);
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, source_id TEXT, url TEXT, payload TEXT NOT NULL,
 status TEXT NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS articles(id TEXT PRIMARY KEY, canonical TEXT UNIQUE, body_hash TEXT UNIQUE,
 day TEXT, topic TEXT, path TEXT UNIQUE, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS daily_allocations(article_id TEXT PRIMARY KEY, day TEXT, topic TEXT);
CREATE TABLE IF NOT EXISTS outbox(article_id TEXT PRIMARY KEY, kb_id TEXT, status TEXT, task_id TEXT,
 document_id TEXT, filename TEXT, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_attempts(id TEXT PRIMARY KEY, article_id TEXT, started TEXT, payload TEXT);
"""

class Store:
    def __init__(self, data):
        self.data = data
        path = data / "state/nyaanews.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        # Even initial schema writes use the backup entry point.
        self.backup()
        self.db.executescript(SCHEMA)
        self.db.commit()

    def backup(self):
        folder = self.data / "backups"
        folder.mkdir(exist_ok=True)
        name = folder / (now().strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8] + ".db")
        with closing(sqlite3.connect(name)) as dst:
            self.db.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Local backup integrity check failed")
        return name

    @contextmanager
    def write(self):
        self.backup()
        try:
            yield self.db
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def source(self, sid):
        row = self.db.execute("SELECT payload FROM sources WHERE id=?", (sid,)).fetchone()
        return json.loads(row[0]) if row else {}

    def put_source(self, sid, obj, candidates):
        with self.write() as db:
            db.execute("INSERT OR REPLACE INTO sources VALUES (?,?)", (sid, json.dumps(obj)))
            for c in candidates:
                db.execute("INSERT OR IGNORE INTO candidates VALUES (?,?,?,?,?,?)",
                           (c["id"], sid, c["url"], json.dumps(c), "discovered", None))

    def candidate_status(self, cid, status, reason=None):
        with self.write() as db:
            db.execute("UPDATE candidates SET status=?,reason=? WHERE id=?", (status, reason, cid))

    def candidates(self):
        rows = self.db.execute("SELECT payload FROM candidates WHERE status IN ('discovered','deferred')").fetchall()
        return [json.loads(r[0]) for r in rows]

    def count(self, day, topic):
        return self.db.execute("SELECT COUNT(*) FROM daily_allocations WHERE day=? AND topic=?",
                               (day, topic)).fetchone()[0]

    def duplicate(self, url, content_hash=None):
        return self.db.execute("SELECT id FROM articles WHERE canonical=? OR body_hash=?",
                               (url, content_hash)).fetchone()

    def register(self, meta, relative, cap):
        with self.write() as db:
            exists = db.execute("SELECT id FROM articles WHERE id=?", (meta["article_id"],)).fetchone()
            if exists:
                return False
            if self.count(meta["target_date"], meta["primary_topic"]) >= cap:
                raise ValueError("daily_cap")
            db.execute("INSERT INTO articles VALUES (?,?,?,?,?,?,?)",
                       (meta["article_id"], meta["canonical_url"], meta["content_sha256"],
                        meta["target_date"], meta["primary_topic"], relative, json.dumps(meta)))
            db.execute("INSERT INTO daily_allocations VALUES (?,?,?)",
                       (meta["article_id"], meta["target_date"], meta["primary_topic"]))
            db.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?,?)",
                       (meta["article_id"], None, "pending", None, None, None, "{}"))
        return True
