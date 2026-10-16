"""Resolve missing arXiv IDs for curated library entries by title search.

A curated entry without an arXiv ID cannot be enriched from a PDF. A strict
title search adds the preprint ID, after which the regular discovery and
revision rotation imports the record so the AI draft step can read the paper.
A match needs a near-identical normalized title and a shared author family
name; anything weaker is recorded and retried after the configured cooldown.
"""
from __future__ import annotations

import difflib
import re
import time
import unicodedata
from pathlib import Path

from .common import ROOT, identity_keys, iso, parse_time, read_json, utcnow, write_json
from .arxiv_source import Client, parse_feed

MARKER = "arxiv_lookup"


def normalized_title(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).lower()
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


def title_score(left, right) -> float:
    a, b = normalized_title(left), normalized_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def family_names(names) -> set:
    out = set()
    for name in names or []:
        parts = re.sub(r"[^A-Za-z\u00c0-\u024f' -]+", " ", str(name)).split()
        if parts:
            out.add(parts[-1].lower())
    return out


def search(client, title: str, max_results: int = 5) -> list:
    raw = client.get({"search_query": 'ti:"%s"' % title[:200], "max_results": str(max_results)})
    entries, _ = parse_feed(raw)
    return entries


def run_seed_lookup(config: dict, root=ROOT, client=None, now=None, limit=None) -> dict:
    cfg = config.get("seed_lookup") or {}
    stamp = iso(now or utcnow())
    report = {"at": stamp, "status": "disabled", "checked": 0, "matched": 0, "none": 0, "errors": []}
    if not cfg.get("enabled"):
        return report
    root = Path(root)
    path = root / "data/seed.json"
    seeds = read_json(path, [])
    store = read_json(root / "data/store.json", {})
    known = set()
    for seed in seeds:
        known |= {key for key in identity_keys(seed) if key.startswith("arxiv:")}
    for record in (store.get("records") or {}).values():
        known |= {key for key in identity_keys(record) if key.startswith("arxiv:")}
    budget = max(0, int(limit)) if limit else max(0, int(cfg.get("max_per_run", 40)))
    min_score = float(cfg.get("min_title_score", 0.97))
    retry_days = int(cfg.get("retry_days", 120))
    seconds = max(0.0, float(cfg.get("max_seconds", 600)))
    error_run = max(1, int(cfg.get("max_consecutive_errors", 5)))
    started = time.monotonic()
    consecutive = 0
    ar = config["arxiv"]
    client = client or Client(ar["request_interval_seconds"], config["user_agent"], ar["timeout_seconds"], ar["attempts"])
    moment = now or utcnow()
    changed = 0
    for seed in seeds:
        if report["checked"] >= budget:
            break
        if (seed.get("identifiers") or {}).get("arxiv"):
            continue
        title = str(seed.get("title") or "").strip()
        if len(normalized_title(title)) < 20:
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
        report["checked"] += 1
        try:
            rows = search(client, title)
        except Exception as error:
            report["errors"].append({"id": seed.get("id"), "type": type(error).__name__})
            consecutive += 1
            if consecutive >= error_run:
                report["aborted"] = "source-unavailable"
                break
            continue
        consecutive = 0
        wanted = family_names(seed.get("authors"))
        best, match = 0.0, None
        for row in rows:
            if wanted and not (family_names(row.get("authors")) & wanted):
                continue
            score = title_score(title, row.get("title"))
            if score > best:
                best, match = score, row
        ids = ((match or {}).get("identifiers") or {}).get("arxiv") or []
        if ids and best >= min_score and ("arxiv:" + ids[0]) not in known:
            bucket = seed.setdefault("identifiers", {}).setdefault("arxiv", [])
            seed["identifiers"]["arxiv"] = sorted(set(bucket) | {ids[0]})
            seed[MARKER] = {"at": stamp, "result": "matched", "score": round(best, 4), "arxiv": ids[0]}
            known.add("arxiv:" + ids[0])
            report["matched"] += 1
        else:
            seed[MARKER] = {"at": stamp, "result": "none", "score": round(best, 4)}
            report["none"] += 1
        changed += 1
    if changed:
        write_json(path, seeds)
    report["status"] = "failed" if report["errors"] and not report["checked"] else ("partial" if report["errors"] else "ok")
    return report
