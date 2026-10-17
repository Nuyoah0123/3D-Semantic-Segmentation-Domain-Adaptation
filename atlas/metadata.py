"""Fill library abstracts and open-access links from OpenAlex metadata.

Curated library entries carry no abstract: the source export was sanitised.
OpenAlex exposes abstracts as an inverted index plus the best open-access PDF
location, so a journal paper without a preprint still gets a marked
abstract-level record, and a paper with a legal open copy can be read by the
evidence-linked draft step. Only public metadata is stored; nothing private is
copied back in.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .common import ROOT, read_json, write_json, iso, utcnow, parse_time
from .seed_lookup import title_score

MARKER = "abstract_lookup"
ENDPOINT = "https://api.openalex.org/works"
UA = "3d-seg-atlas/1.0 (+https://github.com/Nuyoah0123/3D-Semantic-Segmentation-Domain-Adaptation)"


def normalize_doi(text) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.strip()


def reconstruct_abstract(inverted) -> str:
    """OpenAlex stores abstracts as {token: [positions]}; rebuild the text."""
    if not isinstance(inverted, dict) or not inverted:
        return ""
    slots: list[tuple[int, str]] = []
    for token, positions in inverted.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                slots.append((position, token))
    if not slots:
        return ""
    slots.sort()
    return " ".join(token for _, token in slots)


def plain(text) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    return " ".join(value.split())


def work_doi(work: dict) -> str:
    return normalize_doi(work.get("doi") or "")


def work_abstract(work: dict) -> str:
    return plain(reconstruct_abstract(work.get("abstract_inverted_index")))


def work_pdf(work: dict) -> str:
    best = work.get("best_oa_location") or {}
    for key in ("pdf_url", "landing_page_url"):
        url = str(best.get(key) or "").strip()
        if url.startswith("https://") and url.lower().endswith(".pdf"):
            return url
    for location in work.get("locations") or []:
        url = str((location or {}).get("pdf_url") or "").strip()
        if url.startswith("https://") and url.lower().endswith(".pdf"):
            return url
    return ""


def http_json(url: str, timeout: int = 40) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(4_000_001).decode("utf-8", "replace"))


def work_url(config: dict, doi: str = "", title: str = "") -> str:
    mailto = str((config.get("metadata") or {}).get("mailto") or "").strip()
    suffix = ("&mailto=" + urllib.parse.quote(mailto)) if mailto else ""
    if doi:
        return f"{ENDPOINT}/https://doi.org/{urllib.parse.quote(normalize_doi(doi))}?format=json{suffix}"
    query = urllib.parse.quote(plain(title)[:220])
    return f"{ENDPOINT}?format=json&per-page=5&search={query}{suffix}"


def pick_work(payload, min_score: float, title: str = "") -> dict | None:
    if not isinstance(payload, dict):
        return None
    if "results" not in payload:
        return payload or None
    best, score = None, 0.0
    for candidate in payload.get("results") or []:
        current = title_score(title, candidate.get("title") or candidate.get("display_name"))
        if current > score:
            best, score = candidate, current
    return best if best is not None and score >= min_score else None


def run_metadata_backfill(config, root=ROOT, fetch=None, now=None, limit=None) -> dict:
    cfg = config.get("metadata") or {}
    stamp = iso(now or utcnow())
    report = {"at": stamp, "status": "disabled", "checked": 0, "filled": 0, "with_pdf": 0,
              "none": 0, "errors": []}
    if not cfg.get("enabled"):
        return report
    root = Path(root)
    path = root / "data/seed.json"
    seeds = read_json(path, [])
    interval = max(0.0, float(cfg.get("request_interval_seconds", 1.0)))
    budget = max(0, int(limit)) if limit else max(0, int(cfg.get("max_per_run", 200)))
    seconds = max(0.0, float(cfg.get("max_seconds", 300)))
    error_run = max(1, int(cfg.get("max_consecutive_errors", 4)))
    min_score = float(cfg.get("min_title_score", 0.97))
    retry_days = int(cfg.get("retry_days", 120))
    fetch = fetch or http_json
    moment = now or utcnow()
    started = time.monotonic()
    consecutive = 0
    changed = 0
    last_call = 0.0
    for seed in seeds:
        if report["checked"] >= budget:
            break
        if plain(seed.get("abstract")):
            continue
        doi = normalize_doi(((seed.get("identifiers") or {}).get("doi") or [""])[0])
        title = str(seed.get("title") or "").strip()
        if not doi and len(plain(title)) < 20:
            continue
        marker = seed.get(MARKER) or {}
        if marker.get("result") == "none" and marker.get("at"):
            try:
                if (moment - parse_time(marker["at"])).days < retry_days:
                    continue
            except ValueError:
                pass
        if seconds and time.monotonic() - started > seconds:
            report["aborted"] = "time-budget"
            break
        wait = interval - (time.monotonic() - last_call)
        if wait > 0:
            time.sleep(wait)
        last_call = time.monotonic()
        report["checked"] += 1
        try:
            payload = fetch(work_url(config, doi=doi, title=title))
            work = pick_work(payload, min_score, title)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                report["errors"].append({"id": seed.get("id"), "type": "HTTP" + str(error.code)})
                consecutive += 1
                if consecutive >= error_run:
                    report["aborted"] = "source-unavailable"
                    break
                continue
            # Unknown DOI: record a miss so the entry backs off instead of retrying forever.
            work = None
            consecutive = 0
        except Exception as error:
            report["errors"].append({"id": seed.get("id"), "type": type(error).__name__})
            consecutive += 1
            if consecutive >= error_run:
                report["aborted"] = "source-unavailable"
                break
            continue
        consecutive = 0
        abstract = work_abstract(work or {})
        pdf = work_pdf(work or {})
        if abstract or pdf:
            if abstract:
                seed["abstract"] = abstract
                seed["abstract_source"] = "openalex"
                seed["abstract_at"] = stamp
            if pdf:
                seed["oa_pdf"] = pdf
                report["with_pdf"] += 1
            source = ((work or {}).get("primary_location") or {}).get("source") or {}
            venue = plain(source.get("display_name"))
            if venue and not plain(seed.get("venue")):
                seed["venue"] = venue
            year = (work or {}).get("publication_year")
            if year and not plain(seed.get("published")):
                seed["published"] = f"{int(year):04d}-01-01"
                seed["date_basis"] = "openalex"
            seed[MARKER] = {"at": stamp, "result": "ok", "source": "openalex",
                            "abstract": bool(abstract), "pdf": bool(pdf)}
            report["filled"] += 1
        else:
            seed[MARKER] = {"at": stamp, "result": "none", "source": "openalex"}
            report["none"] += 1
        changed += 1
    if changed:
        write_json(path, seeds)
    report["status"] = "failed" if report["errors"] and not report["checked"] else ("partial" if report["errors"] else "ok")
    return report
