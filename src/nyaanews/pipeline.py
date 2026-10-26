from datetime import date
from pathlib import Path
import json
import re
import shutil
import tempfile
import uuid
import time
from filelock import FileLock
from .store import Store
from .network import PublicHTTP
from .feeds import poll
from .extract import extract_article, markdown, read_markdown
from .util import now, parse_date, window, atomic_write, atomic_json, digest

def relevant(source, candidate):
    text = candidate["title"] + " " + candidate.get("summary", "") + " " + candidate["url"]
    return (not source.get("include") or any(re.search(p, text, re.I) for p in source["include"])) and not any(
        re.search(p, text, re.I) for p in source.get("exclude", []))

def recover_files(cfg, store):
    caps = {t["id"]:t.get("max_articles_per_day", 3) for t in cfg.topics}
    for path in (cfg.data / "news").glob("*/*/*/*/*.md"):
        meta = read_markdown(path)
        if store.db.execute("SELECT id FROM articles WHERE id=?", (meta["article_id"],)).fetchone():
            continue
        # Only recover files created by the application, within known caps/topics.
        if meta.get("primary_topic") not in caps or not meta.get("target_date"):
            raise ValueError("unrecognized_orphan_archive")
        text=path.read_text(encoding="utf-8")
        body=text.split("\n## Article\n\n",1)[-1].rstrip("\n")
        if digest(body)!=meta.get("content_sha256"):
            raise ValueError("orphan_content_hash_mismatch")
        store.register(meta, path.relative_to(cfg.data).as_posix(), caps[meta["primary_topic"]])

def archive_run(cfg, day, *, refresh=False, http=None, run_sync=True):
    cfg.prepare()
    lock = FileLock(str(cfg.data / "state/run.lock"), timeout=0)
    with lock:
        temp_parent = Path(tempfile.gettempdir()) / "nyaanews"
        temp_parent.mkdir(exist_ok=True)
        # The data-root lock ensures no active application run is cleaned.
        for old in temp_parent.glob("run-*"):
            if old.is_dir() and not old.is_symlink():
                shutil.rmtree(old)
        with tempfile.TemporaryDirectory(prefix="run-", dir=temp_parent):
            return _run(cfg, day, refresh, http or PublicHTTP(), run_sync)

def _run(cfg, day, refresh, http, run_sync):
    store = Store(cfg.data)
    run_id = uuid.uuid4().hex
    report = {"id":run_id, "day":day.isoformat(), "started":now().isoformat(),
              "sources":[], "topics":{}, "saved":0, "errors":[]}
    failed_topics=set()
    started = time.monotonic()
    deadline = int(cfg.values.get("RUN_BUDGET_SECONDS", "2400"))
    try:
        recover_files(cfg, store)
        with store.write() as db:
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                       (run_id, str(day), report["started"], None, "running", "{}"))
        sources = {s["id"]:s for s in cfg.sources if s.get("enabled", True)}
        topics = {t["id"]:t for t in cfg.topics if t.get("enabled", True)}
        for sid, source in sources.items():
            if time.monotonic() - started > deadline:
                report["sources"].append({"id":sid,"status":"deferred","reason":"run_budget"})
                continue
            result = poll(source, store, http, refresh)
            report["sources"].append(result)
            print(json.dumps({"source":sid,"status":result["status"]}), flush=True)
        start, end = window(day, cfg.tz)
        candidates = sorted(store.candidates(), key=lambda c:(
            sources.get(c["source_id"], {}).get("weight", 1), c.get("published_at") or ""),
            reverse=True)
        for candidate in candidates:
            if time.monotonic() - started > deadline:
                report["errors"].append({"reason":"run_budget"})
                break
            source = sources.get(candidate["source_id"])
            if not source or not relevant(source, candidate):
                continue
            stamp = parse_date(candidate.get("published_at"))
            if stamp and not (start <= stamp < end):
                continue
            possible = [t for t in source["topics"] if t in topics and store.count(str(day),t) <
                        topics[t].get("max_articles_per_day",3)]
            if not possible:
                continue
            if store.duplicate(candidate["url"]):
                store.candidate_status(candidate["id"], "deduplicated")
                continue
            try:
                article = extract_article(http.get(candidate["url"]), candidate, source)
                published = parse_date(article["published_at"])
                if not (start <= published < end):
                    # Preserve for its own day; do not globally reject it.
                    continue
                if store.duplicate(article["canonical_url"], article["content_sha256"]):
                    store.candidate_status(candidate["id"], "deduplicated")
                    continue
                topic = possible[0]
                article.update(primary_topic=topic, tags=possible[1:], target_date=str(day))
                path = cfg.data / "news" / day.strftime("%Y/%m/%d") / topic / (article["article_id"] + ".md")
                atomic_write(path, markdown(article))
                store.register({k:v for k,v in article.items() if k != "body"},
                               path.relative_to(cfg.data).as_posix(), topics[topic].get("max_articles_per_day",3))
                store.candidate_status(candidate["id"], "md_ready")
                report["saved"] += 1
            except Exception as exc:
                failed_topics.update(possible)
                reason = getattr(exc, "kind", None) or (str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
                store.candidate_status(candidate["id"], "deferred", reason[:100])
                report["errors"].append({"candidate":candidate["id"],"reason":reason[:100]})
        source_states = {r["id"]:r["status"] for r in report["sources"]}
        for tid, topic in topics.items():
            count = store.count(str(day), tid)
            linked = [s for s in sources.values() if tid in s["topics"]]
            states = [source_states.get(s["id"],"failed") for s in linked]
            failures = any(s != "succeeded" for s in states) or tid in failed_topics
            status = "partial" if count and failures else "succeeded" if count else (
                "failed" if not states or all(s=="failed" for s in states) else
                "deferred" if failures else "empty")
            report["topics"][tid] = {"status":status,"count":count,
                                    "cap":topic.get("max_articles_per_day",3)}
        if run_sync and cfg.values.get("SYNC_ENABLED") == "1":
            from .sync import sync_pending
            report["sync"] = sync_pending(cfg, store)
        report["status"] = "partial" if report["errors"] or any(
            s["status"] != "succeeded" for s in report["sources"]) or any(
            report.get("sync",{}).get(k,0) for k in ("unknown","failed","pending")) else "completed"
        report["ended"] = now().isoformat()
        with store.write() as db:
            db.execute("UPDATE runs SET ended=?,status=?,report=? WHERE id=?",
                       (report["ended"], report["status"], json.dumps(report), run_id))
        atomic_json(cfg.data / "logs" / (day.isoformat() + "-" + run_id + ".json"), report)
        atomic_json(cfg.data / "state/last-run.json", report)
        return report
    except BaseException:
        report.update(status="interrupted",ended=now().isoformat())
        atomic_json(cfg.data / "state/last-run.json", report)
        raise
    finally:
        store.close()
