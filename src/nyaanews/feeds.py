from datetime import datetime, timedelta, timezone
import json
import calendar
from urllib.parse import urljoin
import feedparser
from .util import digest, normalize_url, parse_date, now
from .network import FetchError

def parse_feed(body, url, sid):
    parsed = feedparser.parse(body, response_headers={"content-location": url,"content-type":"application/xml"})
    if not parsed.get("version") or (parsed.bozo and not parsed.entries):
        raise ValueError("invalid_feed")
    if len(parsed.entries) > 5000:
        raise ValueError("feed_too_many_entries")
    result = []
    for entry in parsed.entries:
        links = entry.get("links", [])
        link = next((l.get("href") for l in links if l.get("rel", "alternate") == "alternate"
                     and l.get("type", "text/html") in ("text/html", "application/xhtml+xml", "")), None)
        link = link or entry.get("link")
        if not link or not entry.get("title"):
            continue
        try:
            link = normalize_url(urljoin(url, link))
        except ValueError:
            continue
        # Updated is not silently substituted for original publication.
        raw = dict(entry).get("published")
        dt = parse_date(raw)
        result.append({
            "id": digest(sid + "\n" + (entry.get("id") or link)),
            "source_id": sid, "url": link, "title": entry.title,
            "published_at": dt.isoformat() if dt else None,
            "published_at_raw": raw, "updated_at": dict(entry).get("updated"),
            "feed_seen_at": now().isoformat(),
            "summary": entry.get("summary", "")[:6000]})
    return result

def poll(source, store, http, refresh=False):
    sid = source["id"]
    old = store.source(sid)
    if old.get("feed_url")!=source["feed_url"]:
        old={"feed_url":source["feed_url"]}
    retry_at = parse_date(old.get("next_retry_at"))
    if retry_at and retry_at > now():
        return {"id": sid, "status": "deferred", "reason": "source_backoff"}
    headers = {}
    if not refresh and old.get("etag"):
        headers["If-None-Match"] = old["etag"]
    if not refresh and old.get("last_modified"):
        headers["If-Modified-Since"] = old["last_modified"]
    try:
        if source.get("feed_http_profile","standard")=="standard":
            response = http.get(source["feed_url"], headers)
        else:
            response = http.get(source["feed_url"], headers,profile=source["feed_http_profile"])
        if response.status == 304:
            old.update(status="succeeded", failures=0, next_retry_at=None, last_success=now().isoformat())
            store.put_source(sid, old, [])
            return {"id": sid, "status": "succeeded", "not_modified": True}
        candidates = parse_feed(response.body, response.url, sid)
        stamps = [c["published_at"] for c in candidates if c["published_at"]]
        watermark = max(stamps + ([old["watermark"]] if old.get("watermark") else []), default=None)
        store.put_source(sid, {"feed_url":source["feed_url"], "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
            "last_success": now().isoformat(), "failures": 0, "next_retry_at": None,
            "watermark": watermark, "status": "succeeded"}, candidates)
        return {"id": sid, "status": "succeeded", "count": len(candidates),
                "missing_dates": sum(c["published_at"] is None for c in candidates)}
    except Exception as exc:
        failures = old.get("failures", 0) + 1
        seconds = min(86400, 300 * 2 ** min(failures - 1, 8))
        kind = exc.kind if isinstance(exc, FetchError) else "invalid_feed"
        status = exc.status if isinstance(exc, FetchError) else 0
        if isinstance(exc, FetchError) and exc.retry_after:
            try:
                seconds = max(seconds, int(exc.retry_after))
            except ValueError:
                value = parse_date(exc.retry_after)
                if value:
                    seconds = max(seconds, int((value - now()).total_seconds()))
        old.update(status="failed", reason=kind, http_status=status, failures=failures,
                   next_retry_at=(now() + timedelta(seconds=seconds)).isoformat())
        store.put_source(sid, old, [])
        return {"id": sid, "status": "failed", "reason": kind, "http_status": status}
