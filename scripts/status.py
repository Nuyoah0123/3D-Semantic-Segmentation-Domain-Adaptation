#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from atlas.common import ROOT,read_json
from atlas.pipeline import EMPTY
if __name__=="__main__":
    s=read_json(ROOT/"data/store.json",EMPTY);r=s.get("last_run",{})
    print("## 3D paper discovery\n")
    print("Status:",r.get("status","not-run"));print("\nLast attempt:",r.get("at","not-run"));print("\nLast complete success:",s.get("last_full_success","none"))
    for source in r.get("sources",[]):print("\n-",source["source"],":",source["status"],"; backlog days:",source.get("backlog_days",0))
    print("\nCounts:",r.get("counts",{}))
    for key in ("llm_last_run","zotero_last_run"):print("\n"+key+":",s.get(key,{}).get("status","not-run"))
