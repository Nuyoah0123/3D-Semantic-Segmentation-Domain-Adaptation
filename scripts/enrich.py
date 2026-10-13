#!/usr/bin/env python3
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from atlas.common import load_config
from atlas.enrich import run_enrichment
if __name__=="__main__":
    r=run_enrichment(load_config());print(json.dumps(r,ensure_ascii=False,indent=2));sys.exit(0 if r["status"] in {"ok","disabled"} else 2)
