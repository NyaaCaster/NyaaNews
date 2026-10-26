from datetime import date
from pathlib import Path
import json
import re
import shutil
import tempfile
import uuid
import time
from filelock import FileLock
from .config import ordered_sources
from .store import Store
from .network import PublicHTTP
from .feeds import poll
from .extract import extract_article, markdown, read_markdown
from .util import now, parse_date, window, atomic_write, atomic_json, digest

def relevant(source, candidate, topic_id=None):
    text = candidate["title"] + " " + candidate.get("summary", "") + " " + candidate["url"]
    def matches(rule):
        return (not rule.get("include") or any(re.search(p,text,re.I) for p in rule["include"])) and not any(
            re.search(p,text,re.I) for p in rule.get("exclude",[])) and (
            not rule.get("title_include") or any(re.search(p,candidate["title"],re.I) for p in rule["title_include"])) and not any(
            re.search(p,candidate["title"],re.I) for p in rule.get("title_exclude",[]))
    return matches(source) and matches(source.get("topic_rules",{}).get(topic_id,{}))

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
        start, end = window(day, cfg.tz)
        polled={}
        attempted={}
        budget_hit=False
        def over_budget():
            return time.monotonic()-started>deadline
        for tid,topic in topics.items():
            cap=topic.get("max_articles_per_day",3)
            order=ordered_sources(topic,cfg.sources)
            used=[]
            selections=[]
            for source in order:
                if store.count(str(day),tid)>=cap:
                    break
                if over_budget():
                    budget_hit=True
                    break
                sid=source["id"]
                used.append(sid)
                if sid not in polled:
                    result=poll(source,store,http,refresh)
                    polled[sid]=result
                    report["sources"].append(result)
                    print(json.dumps({"source":sid,"status":result["status"]}),flush=True)
                selection={"source":sid,"candidates":0,"outside_window":0,"topic_mismatch":0,
                           "duplicates":0,"article_failures":0,"saved":0}
                selections.append(selection)
                candidates=sorted(store.candidates(sid),key=lambda c:c.get("published_at") or "",reverse=True)
                for candidate in candidates:
                    if store.count(str(day),tid)>=cap:
                        break
                    if over_budget():
                        budget_hit=True
                        break
                    selection["candidates"]+=1
                    if not relevant(source,candidate,tid):
                        selection["topic_mismatch"]+=1
                        continue
                    stamp=parse_date(candidate.get("published_at"))
                    if stamp and not (start<=stamp<end):
                        selection["outside_window"]+=1
                        continue
                    if store.duplicate(candidate["url"]):
                        store.candidate_status(candidate["id"],"deduplicated")
                        selection["duplicates"]+=1
                        continue
                    if candidate["id"] in attempted:
                        if attempted[candidate["id"]]=="failed":
                            failed_topics.add(tid)
                            selection["article_failures"]+=1
                        continue
                    attempted[candidate["id"]]="attempted"
                    try:
                        profile=source.get("article_http_profile","standard")
                        response=(http.get(candidate["url"]) if profile=="standard" else
                                  http.get(candidate["url"],profile=profile))
                        article=extract_article(response,candidate,source)
                        published=parse_date(article["published_at"])
                        if not (start<=published<end):
                            selection["outside_window"]+=1
                            continue
                        if store.duplicate(article["canonical_url"],article["content_sha256"]):
                            store.candidate_status(candidate["id"],"deduplicated")
                            selection["duplicates"]+=1
                            continue
                        tags=[other for other in source["topics"] if other!=tid and other in topics
                              and relevant(source,candidate,other)]
                        article.update(primary_topic=tid,tags=tags,target_date=str(day))
                        path=cfg.data/"news"/day.strftime("%Y/%m/%d")/tid/(article["article_id"]+".md")
                        atomic_write(path,markdown(article))
                        store.register({k:v for k,v in article.items() if k!="body"},
                                       path.relative_to(cfg.data).as_posix(),cap)
                        store.candidate_status(candidate["id"],"md_ready")
                        selection["saved"]+=1
                        report["saved"]+=1
                    except Exception as exc:
                        attempted[candidate["id"]]="failed"
                        failed_topics.add(tid)
                        selection["article_failures"]+=1
                        reason=getattr(exc,"kind",None) or (str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
                        status=getattr(exc,"status",0)
                        stored_reason=reason+(f":{status}" if status else "")
                        store.candidate_status(candidate["id"],"deferred",stored_reason[:100])
                        report["errors"].append({"candidate":candidate["id"],"reason":reason[:100],"http_status":status})
            count=store.count(str(day),tid)
            states=[polled[sid]["status"] for sid in used]
            failures=any(state!="succeeded" for state in states) or tid in failed_topics
            status=("partial" if failures else "succeeded") if count else (
                "failed" if not order or (states and all(state=="failed" for state in states)) else
                "deferred" if failures or budget_hit else "empty")
            report["topics"][tid]={"status":status,"count":count,"cap":cap,"remaining":max(0,cap-count),
                "sources_used":used,"sources_skipped":[s["id"] for s in order if s["id"] not in used],
                "stop_reason":"quota_met" if count>=cap else "run_budget" if budget_hit else "sources_exhausted",
                "selection":selections}
        if budget_hit:
            report["errors"].append({"reason":"run_budget"})
        report["target_count"]=sum(t.get("max_articles_per_day",3) for t in topics.values())
        report["collected_count"]=sum(t["count"] for t in report["topics"].values())
        report["remaining_count"]=report["target_count"]-report["collected_count"]
        report["sources_skipped"]=[sid for sid in sources if sid not in polled]
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
