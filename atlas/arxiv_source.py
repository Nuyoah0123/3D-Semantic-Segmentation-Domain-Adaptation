from __future__ import annotations
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from .common import arxiv_id, doi_id, parse_time

NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/", "x": "http://arxiv.org/schemas/atom"}
ENDPOINT = "https://export.arxiv.org/api/query"

class SourceError(RuntimeError):
    pass

class Client:
    def __init__(self, interval: float = 3.2, user_agent: str = "3D-Seg-Daily/1.0", timeout: int = 40, attempts: int = 3):
        self.interval = max(3.0, float(interval))
        self.user_agent = user_agent
        self.timeout = timeout
        self.attempts = attempts
        self.last_call = 0.0

    def get(self, params: dict) -> bytes:
        query = urllib.parse.urlencode(params)
        url = ENDPOINT + "?" + query
        last = None
        for attempt in range(self.attempts):
            time.sleep(max(0.0, self.interval - (time.monotonic() - self.last_call)))
            self.last_call = time.monotonic()
            request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept":"application/atom+xml"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as r:
                    raw = r.read(12_000_001)
                    if len(raw) > 12_000_000:
                        raise SourceError("arXiv response exceeds safety limit")
                    return raw
            except urllib.error.HTTPError as e:
                last = "HTTP " + str(e.code)
                if e.code not in {429, 500, 502, 503, 504}:
                    raise SourceError(last) from e
                delay = min(60, 5 * 2**attempt)
                retry = e.headers.get("Retry-After", "")
                if retry:
                    try:
                        delay = float(retry)
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError):
                            pass
                if delay > 180:
                    raise SourceError("arXiv requested a long Retry-After; retry next run") from e
                if attempt < self.attempts - 1:
                    time.sleep(max(0, delay))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = type(e).__name__ + ": " + str(e)[:180]
                if attempt < self.attempts - 1:
                    time.sleep(min(30, 5 * 2**attempt))
        raise SourceError("arXiv unavailable after retries: " + str(last))

def parse_feed(raw: bytes) -> tuple[list[dict], int]:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise SourceError("DTD/entities are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise SourceError("Malformed Atom response") from e
    if root.tag != "{" + NS["a"] + "}feed":
        raise SourceError("Response is not an Atom feed")
    entries = []
    for el in root.findall("a:entry", NS):
        def tx(path):
            return " ".join((el.findtext(path, "", NS) or "").split())
        raw_id = tx("a:id")
        aid, version = arxiv_id(raw_id)
        if not aid:
            raise SourceError("arXiv error or invalid ID: " + tx("a:summary")[:180])
        title, published, updated = tx("a:title"), tx("a:published"), tx("a:updated")
        if not (title and published and updated):
            raise SourceError("Entry lacks required title/date fields: " + aid)
        try:
            parse_time(published); parse_time(updated)
        except ValueError as e:
            raise SourceError("Invalid entry timestamp: " + aid) from e
        doi = doi_id(tx("x:doi"))
        entries.append({
            "id": "arxiv:" + aid, "title": title,
            "authors": [" ".join((e.text or "").split()) for e in el.findall("a:author/a:name", NS)],
            "abstract": tx("a:summary"), "published": published, "updated": updated,
            "version": version or 1, "identifiers": {"arxiv": [aid], "doi": [doi] if doi else []},
            "url": "https://arxiv.org/abs/" + aid, "pdf": "https://arxiv.org/pdf/" + aid,
            "venue": tx("x:journal_ref") or "arXiv", "comment": tx("x:comment"),
            "categories": [e.get("term", "") for e in el.findall("a:category", NS)],
        })
    total = root.findtext("o:totalResults", None, NS)
    if total is None:
        raise SourceError("Feed lacks totalResults; refusing to mark pagination complete")
    try:
        count = int(total)
    except ValueError as e:
        raise SourceError("Invalid totalResults") from e
    if count < 0:
        raise SourceError("Negative totalResults")
    return entries, count

def query_window(client: Client, query: str, start_at, end_at, page_size: int, max_pages: int) -> list[dict]:
    date_range = f'submittedDate:[{start_at:%Y%m%d%H%M} TO {end_at:%Y%m%d%H%M}]'
    found: dict[str,dict] = {}
    offset = 0
    for _ in range(max_pages):
        entries, total = parse_feed(client.get({"search_query": f"({query}) AND {date_range}", "sortBy":"submittedDate", "sortOrder":"descending", "start":offset, "max_results":page_size}))
        if not entries and total > offset:
            raise SourceError("Empty page before totalResults was exhausted")
        before = len(found)
        for paper in entries:
            if paper["id"] in found:
                raise SourceError("Pagination returned duplicate IDs; retry the bounded window")
            found[paper["id"]] = paper
        offset += len(entries)
        if offset >= total:
            return list(found.values())
        if len(found) == before:
            raise SourceError("Pagination repeated a page; refusing to advance cursor")
    raise SourceError("Query exceeds max_pages_per_query; increase limit or split query/date window")

def revisions(client: Client, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    entries, _ = parse_feed(client.get({"id_list": ",".join(ids), "max_results":len(ids)}))
    returned = {p["identifiers"]["arxiv"][0] for p in entries}
    missing = set(ids) - returned
    if missing:
        raise SourceError("Revision query omitted requested IDs: " + ", ".join(sorted(missing)))
    return entries
