from __future__ import annotations
import copy
import html
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import timedelta
from email.utils import format_datetime
from .common import ROOT, read_json, write_json, iso, utcnow, parse_time, safe_url
from .pipeline import EMPTY

STATES={"candidate","selected","metadata-checked","abstract-checked","full-text-read","rejected"}

def public_records(config, seeds, store, editorial, include_imports=False):
    all_papers={p["id"]:copy.deepcopy(p) for p in seeds}
    all_papers.update(copy.deepcopy(store["records"]))
    result=[]
    pub=config["publication"]
    for pid,p in all_papers.items():
        e=editorial.get(pid,{})
        state=e.get("state",p.get("review_status","candidate"))
        if state=="rejected" or e.get("public") is False:continue
        if state in {"metadata-checked","abstract-checked","full-text-read"} and p.get("version") is not None and e.get("reviewed_version") != p.get("version"):
            state="review-stale"
        imported=p.get("imported",False)
        explicit=e.get("public") is True
        if imported and not (include_imports or pub["publish_imported_references"] or explicit):continue
        if not imported and not (pub["publish_auto_candidates"] or explicit):continue
        cl=copy.deepcopy(p.get("classification",{}))
        if "tracks" in e:cl["tracks"]=e["tracks"]
        if "bucket" in e:cl["bucket"]=e["bucket"]
        if "task" in e:cl["task"]=e["task"]
        if cl.get("bucket")=="excluded" and not explicit:continue
        if cl.get("bucket")=="related" and not (pub["include_related"] or explicit):continue
        # Strict public allowlist. Private source fields are never copied through.
        out={k:p.get(k) for k in ("id","title","authors","published","updated","version","venue","identifiers","first_seen","date_basis")}
        out.update(url=safe_url(p.get("url","")),imported=imported,origin=p.get("origin",""),classification={k:cl.get(k) for k in ("bucket","tracks","task","methods","basis","certainty","note")},review_status=state,full_text_read=state=="full-text-read")
        out["abstract"]=p.get("abstract","") if pub["publish_abstracts"] else ""
        if out["abstract"]:
            # Abstracts come from public metadata (OpenAlex/arXiv); the source is stated on the page.
            out["abstract_source"]=p.get("abstract_source","") or ("arxiv" if (p.get("identifiers") or {}).get("arxiv") else "")
            out["oa_pdf"]=safe_url(p.get("oa_pdf",""))
        # Drafts are opt-in AND marked explicitly; draft does not alter classification or review status.
        draft=p.get("ai_draft")
        if pub["publish_ai_drafts"] and draft and not draft.get("stale") and draft.get("summary_zh"):
            out["ai_draft"]={k:draft.get(k) for k in ("title_zh","summary_zh","problem","method","findings","limitations","role","evidence","model","prompt_version","scope","generated_at","human_approved")}
        if e.get("public_note"):
            out["public_note"]=e["public_note"]
        result.append(out)
    return sorted(result,key=lambda p:(p.get("first_seen") or "",p.get("published") or "",p["id"]),reverse=True)

def to_ris(papers):
    lines=[]
    def clean(x):return " ".join(str(x or "").split())
    for p in papers:
        lines.extend(["TY  - JOUR","TI  - "+clean(p["title"])])
        lines.extend("AU  - "+clean(a) for a in (p.get("authors") or []))
        if p.get("published"):lines.append("PY  - "+clean(p["published"][:4]))
        for doi in (p.get("identifiers") or {}).get("doi",[])[:1]:lines.append("DO  - "+clean(doi))
        if safe_url(p.get("url","")):lines.append("UR  - "+clean(p["url"]))
        if p.get("venue"):lines.append("JO  - "+clean(p["venue"]))
        lines.extend(["ER  - ",""])
    return "\n".join(lines)

def build(config,root=ROOT,out:Path|None=None,include_imports=False,now=None):
    now=now or utcnow()
    out=out or root/"dist"
    out=out.resolve()
    root=root.resolve()
    if out==root or out in root.parents or out.name in {"data","web","atlas","scripts","tests","docs",".github"}:
        raise ValueError("Unsafe output directory")
    out.mkdir(parents=True,exist_ok=True)
    allowed={"index.html","style.css","app.js","data.js","data.json",".nojekyll","catalog.ris","recent.ris","feed.xml","digest.md"}
    unexpected=[p.name for p in out.iterdir() if p.name not in allowed]
    if unexpected:
        raise ValueError("Output contains unexpected files; use a clean output directory: "+", ".join(unexpected))
    seeds=read_json(root/"data/seed.json",[])
    store=read_json(root/"data/store.json",EMPTY)
    editorial=read_json(root/"data/editorial.json",{})
    for pid,e in editorial.items():
        if e.get("state","candidate") not in STATES:raise ValueError("Unknown review state for "+pid)
        if "public" in e and not isinstance(e["public"],bool):raise ValueError("public must be boolean")
        if "tracks" in e and (not isinstance(e["tracks"],list) or any(t not in {"DA","DG","OV","TTA"} for t in e["tracks"])):raise ValueError("Unknown editorial track")
        if "bucket" in e and e["bucket"] not in {"core","related","excluded"}:raise ValueError("Unknown editorial bucket")
    papers=public_records(config,seeds,store,editorial,include_imports)
    visible={p["id"] for p in papers}
    events=[e for e in store["events"] if e["paper_id"] in visible and e["kind"] in {"discovered","revised","metadata-updated"}]
    last=store.get("last_run",{"status":"not-run","at":None,"counts":{},"sources":[]})
    # Status labels and query coverage only, no raw request bodies or credentials.
    payload={"site":{"name":config["name"],"name_zh":config["name_zh"],"timezone":config["display_timezone"],"preview":include_imports},"built_at":iso(now),"status":last,"last_full_success":store.get("last_full_success"),"index_count":len(seeds),"papers":papers,"events":events[-500:],"integrations":{"zotero":store.get("zotero_last_run",{}),"llm":store.get("llm_last_run",{})},"topics":config.get("topics",[]),"policy":{"abstracts":config["publication"]["publish_abstracts"],"ai_drafts":config["publication"]["publish_ai_drafts"]}}
    serialized=json.dumps(payload,ensure_ascii=False,separators=(",",":")).replace("<","\\u003c").replace("\u2028","\\u2028").replace("\u2029","\\u2029")
    (out/"data.js").write_text("window.ATLAS = "+serialized+";\n",encoding="utf-8")
    write_json(out/"data.json",payload)
    for name in ("index.html","style.css","app.js"):
        shutil.copyfile(root/"web"/name,out/name)
    (out/".nojekyll").write_text("")
    (out/"catalog.ris").write_text(to_ris(papers),encoding="utf-8")
    cutoff=now-timedelta(days=7)
    new_ids={e["paper_id"] for e in events if e["kind"]=="discovered" and parse_time(e["at"])>=cutoff}
    recent=[p for p in papers if p["id"] in new_ids]
    (out/"recent.ris").write_text(to_ris(recent),encoding="utf-8")
    base=safe_url(config.get("site_url",""))
    rss=ET.Element("rss",version="2.0");channel=ET.SubElement(rss,"channel")
    for tag,value in (("title",config["name_zh"]),("link",base or "https://arxiv.org/"),("description","三维分割研究候选；自动发现，不代表精读或质量背书。"),("lastBuildDate",format_datetime(now))):ET.SubElement(channel,tag).text=value
    for ev in reversed(events[-100:]):
        paper=next(p for p in papers if p["id"]==ev["paper_id"])
        item=ET.SubElement(channel,"item")
        label={"discovered":"新收录","revised":"新版本","metadata-updated":"题录更新"}[ev["kind"]]
        ET.SubElement(item,"title").text=f"[{label}] {paper['title']}"
        ET.SubElement(item,"link").text=paper["url"] or base or "https://arxiv.org/"
        ET.SubElement(item,"guid",isPermaLink="false").text=ev["key"]
        ET.SubElement(item,"pubDate").text=format_datetime(parse_time(ev["at"]))
        ET.SubElement(item,"description").text="题录/摘要初筛；未自动核实实验协议。"
    (out/"feed.xml").write_bytes(ET.tostring(rss,encoding="utf-8",xml_declaration=True))
    digest_lines=["# 本次更新", "",f"构建时间：{iso(now)}",f"抓取状态：{last['status']}",f"上次完整抓取成功：{store.get('last_full_success') or '尚无记录'}","","## 近七天首次收录（不是论文首发日）",""]
    for p in recent:digest_lines.append(f"- {p['title']} — {p['url']}")
    if not recent:digest_lines.append("没有可公开的新收录。请同时检查抓取状态，不能据此推断没有新论文。")
    (out/"digest.md").write_text("\n".join(digest_lines)+"\n",encoding="utf-8")
    return {"output":str(out),"public_records":len(papers),"indexed_imports":len(seeds),"status":last["status"],"preview":include_imports}
