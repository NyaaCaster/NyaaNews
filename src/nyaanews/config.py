"""Private configuration is data, never an executable program."""
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os
import re
from dotenv import dotenv_values

ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))

def validate_items(items, kind):
    ids = set()
    for item in items:
        name = item.get("id", "")
        if not ID.fullmatch(name) or name in ids:
            raise ValueError(f"Invalid or duplicate {kind} id")
        ids.add(name)
    return ids

def ordered_sources(topic, sources):
    linked={s["id"]:s for s in sources if s.get("enabled",True) and topic["id"] in s["topics"]}
    order=topic.get("source_order")
    if order is None:
        return sorted(linked.values(),key=lambda s:-s.get("weight",1))
    return [linked[sid] for sid in order if sid in linked]

@dataclass
class Config:
    path: Path
    values: dict
    topics: list
    sources: list
    data: Path
    tz: ZoneInfo

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        values = dict(dotenv_values(path, interpolate=False))
        for key in ("DATA_DIR", "NYAANEWS_CONFIG", "REQUIRE_MOUNT"):
            if os.environ.get(key):
                values[key] = os.environ[key]
        base = path.parent
        topics = read_json(base / values.get("TOPICS_FILE", "topics.json"))["topics"]
        sources = read_json(base / values.get("SOURCES_FILE", "sources.json"))["sources"]
        ids = validate_items(topics, "topic")
        source_ids=validate_items(sources, "source")
        for topic in topics:
            cap = topic.get("max_articles_per_day", 3)
            if type(cap) is not int or not 0 <= cap <= 50:
                raise ValueError("Invalid daily cap")
        for source in sources:
            for field in ("article_http_profile","feed_http_profile"):
                if source.get(field,"standard") not in ("standard","chrome"):
                    raise ValueError("Invalid HTTP profile")
            if not source.get("topics") or not set(source["topics"]).issubset(ids):
                raise ValueError("Source has missing or unknown topics")
            if not source.get("feed_url", "").startswith(("https://", "http://")):
                raise ValueError("Source needs HTTP(S) feed URL")
            for field in ("include","exclude","title_include","title_exclude"):
                for pattern in source.get(field,[]):
                    re.compile(pattern, re.I)
            rules=source.get("topic_rules",{})
            if not isinstance(rules,dict) or not set(rules).issubset(source["topics"]):
                raise ValueError("Invalid per-topic rules")
            for rule in rules.values():
                for field in ("include","exclude","title_include","title_exclude"):
                    for pattern in rule.get(field,[]):
                        re.compile(pattern,re.I)
        by_id={s["id"]:s for s in sources}
        for topic in topics:
            order=topic.get("source_order")
            if order is not None:
                if not isinstance(order,list) or len(order)!=len(set(order)) or not set(order).issubset(source_ids):
                    raise ValueError("Invalid topic source order")
                if any(topic["id"] not in by_id[sid]["topics"] for sid in order):
                    raise ValueError("Ordered source does not serve topic")
        data_value = values.get("DATA_DIR")
        if not data_value:
            raise ValueError("DATA_DIR is required")
        return cls(path, values, topics, sources, Path(data_value).resolve(),
                   ZoneInfo(values.get("TZ", "Asia/Shanghai")))

    def prepare(self):
        if self.values.get("REQUIRE_MOUNT") == "1" and not os.path.ismount(self.data):
            raise ValueError("DATA_DIR must be a mounted volume")
        if not self.data.is_dir():
            raise ValueError("DATA_DIR does not exist")
        for name in ("news", "state", "logs", "pending", "backups"):
            (self.data / name).mkdir(exist_ok=True)

    def public_status(self):
        return {"topics": sum(t.get("enabled", True) for t in self.topics),
                "sources": sum(s.get("enabled", True) for s in self.sources),
                "timezone": str(self.tz), "data": str(self.data)}
