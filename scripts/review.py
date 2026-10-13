#!/usr/bin/env python3
"""Edit persistent editorial overrides; automatic discovery never overwrites this file."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from atlas.common import ROOT,read_json,write_json,iso
from atlas.site import STATES
if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("paper_id")
    p.add_argument("--state",choices=sorted(STATES))
    p.add_argument("--public",choices=["yes","no"])
    p.add_argument("--tracks",nargs="*",choices=["DA","DG","OV","TTA"])
    p.add_argument("--bucket",choices=["core","related","excluded"])
    p.add_argument("--public-note",help="This note may be published. Never enter private research here.")
    a=p.parse_args()
    known={x["id"]:x for x in read_json(ROOT/"data/seed.json",[])}
    known.update(read_json(ROOT/"data/store.json",{"records":{}})["records"])
    if a.paper_id not in known:p.error("Unknown paper ID; copy ID from the website")
    path=ROOT/"data/editorial.json";data=read_json(path,{})
    e=data.setdefault(a.paper_id,{})
    if a.state:
        e["state"]=a.state
        if a.state in {"metadata-checked","abstract-checked","full-text-read"}:
            e["reviewed_version"]=known[a.paper_id].get("version")
    if a.public:e["public"]=a.public=="yes"
    if a.tracks is not None:e["tracks"]=a.tracks
    if a.bucket:e["bucket"]=a.bucket
    if a.public_note is not None:e["public_note"]=a.public_note
    e["edited_at"]=iso();write_json(path,data)
    print(json.dumps(e,ensure_ascii=False,indent=2))
