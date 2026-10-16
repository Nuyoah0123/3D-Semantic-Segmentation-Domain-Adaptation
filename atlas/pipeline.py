from __future__ import annotations
import copy
from datetime import timedelta
from pathlib import Path
from .common import ROOT, read_json, write_json, iso, parse_time, utcnow, identity_keys, title_key, digest
from .triage import classify
from .arxiv_source import Client, query_window, revisions

EMPTY = {"schema_version": 1, "records": {}, "queries": {}, "events": [], "revision_cursor": 0, "runs": []}

def index_papers(papers: list[dict]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for p in papers:
        for k in identity_keys(p):
            index.setdefault(k, set()).add(p["id"])
    return index

def event(store: dict, kind: str, paper: dict, now: str, **extra) -> None:
    key = digest([kind, paper["id"], paper.get("version"), paper.get("updated"), extra])[:24]
    if any(e["key"] == key for e in store["events"]):
        return
    store["events"].append({"key":key, "kind":kind, "paper_id":paper["id"], "title":paper["title"], "at":now, **extra})

BUCKET_RANK = {"excluded": 0, "related": 1, "core": 2}

def merge_classification(old: dict | None, new: dict, imported: bool) -> dict:
    """Imported library entries keep their curated bucket; automatic labels may only add.

    A crawler match re-runs the keyword classifier on the abstract. For previously
    curated references that must not silently downgrade them: an imported "core"
    or "related" entry stays visible even when the auto keywords miss.
    """
    if not old or not isinstance(old, dict):
        return new
    merged = dict(new)
    merged["tracks"] = sorted(set(old.get("tracks", [])) | set(new.get("tracks", [])))
    if imported:
        if BUCKET_RANK.get(old.get("bucket"), 0) >= BUCKET_RANK.get(new.get("bucket"), 0):
            merged["bucket"] = old.get("bucket", new.get("bucket"))
        if old.get("task") in {"semantic", "adjacent"} and new.get("task") in {"other", "segmentation-unspecified"}:
            merged["task"] = old["task"]
    return merged

def merge_paper(store: dict, seeds: list[dict], incoming: dict, now: str) -> str:
    """Strong identifiers merge; similar titles merely create a review warning."""
    seed_map = {p["id"]: p for p in seeds}
    combined = dict(seed_map)
    combined.update(store["records"])
    index = index_papers(list(combined.values()))
    matches = set()
    for k in identity_keys(incoming):
        matches |= index.get(k, set())
    if len(matches) > 1:
        event(store, "identity-conflict", incoming, now, matching_ids=sorted(matches))
        return "conflict"
    pid = next(iter(matches)) if matches else incoming["id"]
    old = store["records"].get(pid)
    seed = seed_map.get(pid)
    prev = old or seed
    p = copy.deepcopy(incoming)
    p["id"] = pid
    p["imported"] = bool(seed or (old and old.get("imported")))
    p["origin"] = "arxiv"
    p["first_seen"] = old.get("first_seen", now) if old else now
    p["last_seen"] = now
    reference = (seed or {}).get("classification") or (old or {}).get("classification")
    p["classification"] = merge_classification(reference, classify(p["title"], p.get("abstract", "")), bool(seed or (old and old.get("imported"))))
    p["review_status"] = (old or {}).get("review_status", "candidate")
    p["full_text_read"] = False
    p["warnings"] = list((old or {}).get("warnings", []))
    p["versions"] = copy.deepcopy((old or {}).get("versions", []))
    if old:
        # A stale/cache result must not replace a newer version or the same revision's later metadata.
        if p.get("version",1) < old.get("version",1) or (p.get("version",1) == old.get("version",1) and parse_time(p["updated"]) < parse_time(old["updated"])):
            old["last_seen"] = now
            return "unchanged"
        for k in ["ai_draft", "ai_history"]:
            if k in old:
                p[k] = copy.deepcopy(old[k])
        if old.get("ai_draft") and digest([p["title"],p.get("abstract", ""),p.get("version")]) != old["ai_draft"].get("source_hash"):
            p["ai_draft"]["stale"] = True
    if prev:
        for k in ("arxiv", "doi"):
            p["identifiers"][k] = sorted(set(p["identifiers"].get(k, [])) | set(prev.get("identifiers", {}).get(k, [])))
    if not prev:
        for other in combined.values():
            if title_key(other["title"]) == title_key(p["title"]) and other["id"] != pid:
                warning = "相同题名但未证实同一身份：" + other["id"]
                if warning not in p["warnings"]:
                    p["warnings"].append(warning)
        event(store, "discovered", p, now)
        kind = "new"
    elif old and p.get("version", 1) > old.get("version", 1):
        p["versions"].append({k:old.get(k) for k in ("title", "version", "updated", "published")})
        event(store, "revised", p, now, previous_version=old.get("version",1))
        kind = "revised"
    elif not old:
        event(store, "indexed-existing", p, now)
        kind = "existing"
    elif digest([old.get(k) for k in ("title","abstract","venue","comment")]) != digest([p.get(k) for k in ("title","abstract","venue","comment")]):
        event(store, "metadata-updated", p, now)
        kind = "metadata"
    else:
        kind = "unchanged"
    store["records"][pid] = p
    return kind

def run_update(config: dict, root: Path = ROOT, client=None, now=None, days: int | None = None, check_revisions: bool = True) -> dict:
    now = now or utcnow()
    timestamp = iso(now)
    seeds = read_json(root / "data/seed.json", [])
    store = read_json(root / "data/store.json", EMPTY)
    ar = config["arxiv"]
    client = client or Client(ar["request_interval_seconds"], config["user_agent"], ar["timeout_seconds"], ar["attempts"])
    stats = {"new":0, "revised":0, "existing":0, "metadata":0, "unchanged":0, "conflict":0}
    outcomes = []
    for q in ar["queries"]:
        if not q.get("enabled", True):
            continue
        qid, query = q["id"], q["query"]
        previous = store["queries"].get(qid, {})
        same = previous.get("query_hash") == digest(query)
        if days is not None:
            start = now - timedelta(days=days)
        elif same and previous.get("completed_until"):
            start = parse_time(previous["completed_until"]) - timedelta(days=ar["overlap_days"])
        else:
            start = now - timedelta(days=ar["initial_lookback_days"])
        stop = min(now, start + timedelta(days=ar["max_window_days"]))
        result = {"source":qid, "start":iso(start), "end":iso(stop), "backlog_days":max(0,(now-stop).days)}
        try:
            papers = query_window(client, query, start, stop, ar["page_size"], ar["max_pages_per_query"])
            staged = copy.deepcopy(store)
            local_stats = dict.fromkeys(stats,0)
            for paper in papers:
                local_stats[merge_paper(staged, seeds, paper, timestamp)] += 1
            store = staged
            for k,v in local_stats.items():
                stats[k] += v
            # Explicit historical backfill must never regress a later cursor.
            cursor = stop
            if same and previous.get("completed_until"):
                cursor = max(cursor, parse_time(previous["completed_until"]))
            store["queries"][qid] = {"query_hash":digest(query), "completed_until":iso(cursor), "last_success":timestamp}
            result.update(status="ok", matched=len(papers))
        except Exception as e:
            result.update(status="failed", error=(type(e).__name__ + ": " + str(e))[:400])
        outcomes.append(result)
    if not outcomes:
        outcomes.append({"source":"configuration", "status":"failed", "error":"No enabled queries"})
    if check_revisions and ar["revision_batch_size"] > 0:
        all_papers = {p["id"]: p for p in seeds}
        all_papers.update(store["records"])
        ids = sorted({k.split(":",1)[1] for p in all_papers.values() for k in identity_keys(p) if k.startswith("arxiv:")})
        if ids:
            cursor = store.get("revision_cursor",0) % len(ids)
            count = min(ar["revision_batch_size"],len(ids))
            batch = [ids[(cursor+i)%len(ids)] for i in range(count)]
            try:
                papers = revisions(client, batch)
                staged = copy.deepcopy(store)
                local_stats = dict.fromkeys(stats,0)
                for paper in papers:
                    local_stats[merge_paper(staged, seeds, paper, timestamp)] += 1
                store = staged
                for k,v in local_stats.items(): stats[k] += v
                outcomes.append({"source":"known-revisions", "status":"ok", "checked":len(papers), "total_known":len(ids)})
            except Exception as e:
                outcomes.append({"source":"known-revisions", "status":"failed", "error":(type(e).__name__+": "+str(e))[:400]})
            # A withdrawn/missing ID should not block all other IDs forever. Failed batches are retried next rotation.
            store["revision_cursor"] = (cursor+count) % len(ids)
    failed = sum(o["status"] == "failed" for o in outcomes)
    state = "failed" if failed == len(outcomes) else "partial" if failed else "ok"
    report = {"at":timestamp, "status":state, "counts":stats, "sources":outcomes,
              "note":"新增指本站首次发现；修订另计；关键词初筛不等于全文审核。"}
    store["last_run"] = report
    if state == "ok":
        store["last_full_success"] = timestamp
    store["runs"] = (store.get("runs",[]) + [report])[-90:]
    store["events"] = store["events"][-5000:]
    write_json(root / "data/store.json", store)
    return report
