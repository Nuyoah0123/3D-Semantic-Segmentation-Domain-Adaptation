from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def parse_time(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

def read_json(path: Path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8-sig"))

def write_json(path: Path, value) -> None:
    """Atomic replace: a crash cannot leave a half-written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def arxiv_id(value: str) -> tuple[str, int | None]:
    value = re.sub(r"\.pdf(?=$|[?#])", "", unquote(str(value)).strip(), flags=re.I)
    m = re.search(r"(?<!\d)(\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v(\d+))?(?=$|[\s/?#])", value, re.I)
    return (m.group(1).lower(), int(m.group(2)) if m.group(2) else None) if m else ("", None)

def doi_id(value: str) -> str:
    v = unquote(str(value)).strip().lower()
    v = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", v)
    return v if re.match(r"^10\.\d{4,9}/\S+$", v) else ""

def identity_keys(paper: dict) -> set[str]:
    keys = set()
    ids = paper.get("identifiers", {})
    for raw in ids.get("arxiv", []):
        aid, _ = arxiv_id(raw)
        if aid:
            keys.add("arxiv:" + aid)
    for raw in ids.get("doi", []):
        doi = doi_id(raw)
        if doi:
            keys.add("doi:" + doi)
            if doi.startswith("10.48550/arxiv."):
                aid, _ = arxiv_id(doi.split("arxiv.", 1)[1])
                if aid:
                    keys.add("arxiv:" + aid)
    return keys

def title_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.casefold())

def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def safe_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        return value if parts.scheme in {"https", "http"} and parts.hostname and not parts.username else ""
    except (TypeError, ValueError):
        return ""

def load_config(root: Path = ROOT) -> dict:
    c = read_json(root / "config.json")
    if not isinstance(c, dict):
        raise ValueError("config.json must be an object")
    ar = c["arxiv"]
    if not 1 <= int(ar["page_size"]) <= 2000:
        raise ValueError("arxiv.page_size must be 1..2000")
    if float(ar["request_interval_seconds"]) < 3:
        raise ValueError("arXiv requests must be separated by at least 3 seconds")
    if int(ar["max_pages_per_query"]) < 1:
        raise ValueError("max_pages_per_query must be positive")
    if not 0 <= int(ar["revision_batch_size"]) <= 100:
        raise ValueError("revision_batch_size must be 0..100")
    names = [x["id"] for x in ar["queries"]]
    if len(set(names)) != len(names) or not names:
        raise ValueError("Query IDs must be present and unique")
    return c
