from datetime import datetime, date, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import hashlib
import json
import os
import tempfile
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

UTC = timezone.utc

def now():
    return datetime.now(UTC)

def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def parse_date(raw):
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        try:
            value = parsedate_to_datetime(str(raw))
        except (ValueError, TypeError, OverflowError):
            return None
    if value.tzinfo is None:
        return None
    return value.astimezone(UTC)

def window(day: date, tz):
    return (datetime.combine(day, time.min, tz).astimezone(UTC),
            datetime.combine(day + timedelta(days=1), time.min, tz).astimezone(UTC))

def normalize_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("Invalid article URL")
    host = parts.hostname.encode("idna").decode().lower()
    if ":" in host:
        host = f"[{host}]"
    if parts.port and parts.port != (443 if parts.scheme == "https" else 80):
        host += f":{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((parts.scheme, host, parts.path or "/", urlencode(query), ""))

def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def atomic_json(path, obj):
    atomic_write(Path(path), json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
