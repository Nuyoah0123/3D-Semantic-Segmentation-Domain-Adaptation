"""Optional read-only collection import. No Zotero write request exists in this module."""
from __future__ import annotations
import json
import os
import re
import time
import urllib.request
import urllib.error
from .common import ROOT, read_json, write_json, identity_keys, arxiv_id, doi_id, digest, iso, safe_url
from .triage import classify
from .pipeline import index_papers, EMPTY

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected Zotero API redirect; refusing to forward credentials")

def get_collection(library_type, library_id, collection, key, max_pages=20):
    if library_type not in {"users","groups"} or not re.fullmatch(r"\d+",library_id) or not re.fullmatch(r"[A-Z0-9]{8}",collection):
        raise ValueError("Specify valid Zotero library type, numeric ID and 8-character collection key")
    opener=urllib.request.build_opener(NoRedirect())
    records=[];seen=set();base_version=None
    for page in range(max_pages):
        url=f"https://api.zotero.org/{library_type}/{library_id}/collections/{collection}/items/top?format=json&limit=100&start={len(records)}"
        req=urllib.request.Request(url,headers={"Zotero-API-Key":key,"Zotero-API-Version":"3","User-Agent":"3D-Seg-Daily/1.0"},method="GET")
        for attempt in range(3):
            try:
                with opener.open(req,timeout=40) as res:
                    raw=res.read(10_000_001)
                    if len(raw)>10_000_000:raise ValueError("Zotero response too large")
                    rows=json.loads(raw)
                    total=int(res.headers["Total-Results"])
                    version=res.headers.get("Last-Modified-Version")
                    backoff=float(res.headers.get("Backoff",0))
                if backoff>180:raise ValueError("Zotero requested a long backoff; retry next run")
                if backoff>0:time.sleep(backoff)
                break
            except urllib.error.HTTPError as e:
                if e.code not in {429,503} or attempt==2:raise
                wait=float(e.headers.get("Retry-After",5*2**attempt))
                if wait>180:raise ValueError("Zotero requested a long Retry-After") from e
                time.sleep(max(0,wait))
        if not isinstance(rows,list):raise ValueError("Unexpected Zotero response")
        if base_version is not None and version!=base_version:raise ValueError("Zotero library changed during pagination; retry without publishing partial snapshot")
        base_version=version
        for row in rows:
            item_key=row.get("key")
            if not item_key or item_key in seen:raise ValueError("Missing or repeated Zotero key")
            seen.add(item_key)
        records.extend(rows)
        if len(records)>=total:return records
        if not rows:raise ValueError("Unexpected empty Zotero page")
        time.sleep(0.5)
    raise ValueError("Zotero collection exceeds max_pages; no partial import committed")

def sanitize_item(row, library_type, library_id):
    d=row.get("data",{})
    if d.get("itemType") in {"attachment","note","annotation"} or not d.get("title"):return None
    url=safe_url(d.get("url",""))
    doi=doi_id(d.get("DOI",""))
    aid,_=arxiv_id(url)
    if not aid and doi.startswith("10.48550/arxiv."):aid,_=arxiv_id(doi.split("arxiv.",1)[1])
    authors=[c.get("name") or " ".join([c.get("firstName",""),c.get("lastName","")]).strip() for c in d.get("creators",[]) if c.get("creatorType")=="author"]
    # No raw collection name, item key, notes, tags, abstract, attachment paths or credentials are retained.
    classification=classify(d["title"],d.get("abstractNote",""))
    classification["evidence"]=[]
    classification["basis"]="zotero-title-abstract-transient"
    allowed={"track:da":"DA","track:dg":"DG","track:ov":"OV","track:tta":"TTA"}
    tagged={allowed[t["tag"].casefold()] for t in d.get("tags",[]) if isinstance(t,dict) and str(t.get("tag","")).casefold() in allowed}
    if tagged:classification["tracks"]=sorted(tagged);classification["basis"]="zotero-explicit-track-tags"
    raw_date=str(d.get("date",""))
    m=re.fullmatch(r"(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?",raw_date)
    return {"id":"zref:"+digest([library_type,library_id,row["key"]])[:20],"title":d["title"],"authors":authors,"published":raw_date if m else "","date_basis":"imported_bibliographic_date","identifiers":{"arxiv":[aid] if aid else [],"doi":[doi] if doi else []},"url":url or ("https://doi.org/"+doi if doi else ""),"venue":d.get("publicationTitle") or d.get("proceedingsTitle") or "","imported":True,"origin":"zotero-readonly-sync","review_status":"imported-unreviewed","full_text_read":False,"classification":classification}

def run_sync(config,root=ROOT,environ=None,fetcher=get_collection):
    env=os.environ if environ is None else environ
    cfg=config["zotero"]
    report={"at":iso(),"status":"disabled","added":0,"matched":0,"conflicts":0}
    store=read_json(root/"data/store.json",EMPTY)
    if cfg["enabled"]:
        try:
            key=env.get("ZOTERO_API_KEY","")
            lid=env.get("ZOTERO_LIBRARY_ID","") or cfg["library_id"]
            col=env.get("ZOTERO_COLLECTION_KEY","") or cfg["collection_key"]
            if not key:raise ValueError("ZOTERO_API_KEY is required; use read-only library permissions")
            if cfg.get("read_only") is not True:raise ValueError("Only read-only synchronization is implemented")
            rows=fetcher(cfg["library_type"],lid,col,key,cfg["max_pages"])
            seeds=read_json(root/"data/seed.json",[])
            lookup={p["id"]:p for p in seeds}
            lookup.update(store["records"])
            idx=index_papers(list(lookup.values()))
            for row in rows:
                p=sanitize_item(row,cfg["library_type"],lid)
                if not p:continue
                matches=set()
                for k in identity_keys(p):matches |= idx.get(k,set())
                if p["id"] in lookup:matches.add(p["id"])
                if len(matches)>1:report["conflicts"]+=1;continue
                if matches:
                    # Additive import: do not overwrite a prior curated title, taxonomy or identity.
                    report["matched"]+=1;continue
                seeds.append(p);lookup[p["id"]]=p
                for k in identity_keys(p):idx.setdefault(k,set()).add(p["id"])
                report["added"]+=1
            write_json(root/"data/seed.json",seeds)
            report["status"]="partial" if report["conflicts"] else "ok"
        except Exception as e:
            report.update(status="failed",error=type(e).__name__+"; check collection configuration, API permissions and network")
    store["zotero_last_run"]=report;write_json(root/"data/store.json",store)
    return report
