from datetime import datetime
from zoneinfo import ZoneInfo
import json
import re
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
import trafilatura
import yaml
from .util import parse_date, normalize_url, digest, now, UTC

def raw_publication(soup):
    for prop in ("article:published_time", "og:published_time", "datePublished"):
        node = soup.find("meta", attrs={"property":prop}) or soup.find("meta", attrs={"name":prop})
        if node and node.get("content"):
            return node["content"]
    def search(obj):
        if isinstance(obj, dict):
            if obj.get("datePublished"):
                return obj["datePublished"]
            for value in obj.values():
                found = search(value)
                if found:
                    return found
        if isinstance(obj, list):
            for value in obj:
                found = search(value)
                if found:
                    return found
        return None
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            found = search(json.loads(script.string or script.get_text()))
            if found:
                return found
        except (ValueError, TypeError, RecursionError):
            continue
    # Scoped article time only, never recommended-post timestamps.
    node = soup.select_one("article time[datetime]")
    return node.get("datetime") if node else None

def publication(raw, source):
    dt = parse_date(raw)
    if dt:
        return dt, "second"
    # Explicit source timezone is required for a naive timestamp.
    if raw and source.get("source_timezone"):
        try:
            value = datetime.fromisoformat(str(raw))
            if value.tzinfo is None and "T" in raw:
                return value.replace(tzinfo=ZoneInfo(source["source_timezone"])).astimezone(UTC), "second"
        except (ValueError, TypeError):
            pass
    return None, "unknown"

def extract_article(response, candidate, source):
    ctype = response.headers.get("content-type", "").lower()
    if ctype and not any(x in ctype for x in ("html", "xhtml")):
        raise ValueError("not_html")
    soup = BeautifulSoup(response.body, "html.parser")
    # Only accept same-host canonical URLs; relative canonicals are resolved.
    canonical = normalize_url(response.url)
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        proposed = normalize_url(urljoin(response.url, link["href"]))
        if urlsplit(proposed).hostname == urlsplit(response.url).hostname:
            canonical = proposed
    raw = raw_publication(soup)
    stamp, precision = publication(raw, source)
    evidence = "page"
    if not stamp:
        # A community posting time does not establish the original page date.
        if source.get("community"):
            raise ValueError("unverified_original_publication")
        stamp = parse_date(candidate.get("published_at"))
        precision, evidence = "second", "publisher_feed"
        raw = candidate.get("published_at_raw")
    if not stamp:
        raise ValueError("publication_time_unknown")
    body = trafilatura.extract(str(soup), url=response.url, output_format="markdown",
        include_comments=False, include_tables=True, include_formatting=True,
        include_links=True, include_images=False, deduplicate=False)
    if not body or len(body.strip()) < int(source.get("min_body_chars", 400)):
        raise ValueError("extraction_incomplete")
    def plain(value):
        return re.sub(r"\s+", "", BeautifulSoup(value, "html.parser").get_text())
    captions = [n.get_text(" ", strip=True) for n in soup.select("article figcaption")]
    for caption in dict.fromkeys(captions):
        if caption and plain(caption) not in plain(body):
            body += "\n\n" + caption
    h1 = soup.select_one("article h1, main h1.entry-title, h1.entry-title")
    og = soup.find("meta", attrs={"property": "og:title"})
    title = (h1.get_text(" ", strip=True) if h1 else
             (og.get("content", "").strip() if og else "")) or candidate["title"]
    if len(title) > 500 or any(x in title.lower() for x in ("just a moment", "access denied")):
        raise ValueError("blocked_page")
    body=body.strip()
    return {
        "title": title, "body": body, "canonical_url": canonical,
        "source_url": candidate["url"], "source_name": urlsplit(response.url).hostname,
        "source_id": source["id"], "source_feed": source["feed_url"],
        "language": (soup.html.get("lang") if soup.html else None),
        "published_at": stamp.isoformat(), "published_at_raw": raw,
        "published_precision": precision, "publication_evidence": evidence,
        "feed_seen_at": candidate["feed_seen_at"], "fetched_at": now().isoformat(),
        "content_sha256": digest(body), "article_id": digest(canonical)[:32],
        "schema_version": 1}

def markdown(article):
    meta = {k:v for k,v in article.items() if k != "body"}
    return ("---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip() +
            "\n---\n\n# " + article["title"].replace("\n", " ") +
            "\n\n- Source: " + article["canonical_url"] +
            "\n- Published: " + article["published_at"] +
            "\n- Collected: " + article["fetched_at"] +
            "\n\n## Article\n\n" + article["body"].strip() + "\n")

def read_markdown(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("invalid_archive")
    _, header, _ = text.split("---", 2)
    return yaml.safe_load(header)
