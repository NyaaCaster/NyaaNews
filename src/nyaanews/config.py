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
        validate_items(sources, "source")
        for topic in topics:
            cap = topic.get("max_articles_per_day", 3)
            if type(cap) is not int or not 0 <= cap <= 50:
                raise ValueError("Invalid daily cap")
        for source in sources:
            if source.get("article_http_profile","standard") not in ("standard","chrome"):
                raise ValueError("Invalid article HTTP profile")
            if not source.get("topics") or not set(source["topics"]).issubset(ids):
                raise ValueError("Source has missing or unknown topics")
            if not source.get("feed_url", "").startswith(("https://", "http://")):
                raise ValueError("Source needs HTTP(S) feed URL")
            for pattern in source.get("include", []) + source.get("exclude", []):
                re.compile(pattern, re.I)
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
