#!/usr/bin/env python3
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from atlas.common import ROOT,load_config
from atlas.site import build
if __name__=="__main__":
    p=argparse.ArgumentParser(description="Build the public static site using a field allowlist")
    p.add_argument("--include-imports",action="store_true",help="Private local preview only; include sanitized imported references")
    p.add_argument("--out",type=Path)
    a=p.parse_args()
    if a.include_imports and not a.out:p.error("Private preview requires a separate --out directory")
    c=load_config()
    if os.environ.get("ATLAS_SITE_URL"):c["site_url"]=os.environ["ATLAS_SITE_URL"]
    print(json.dumps(build(c,out=a.out,include_imports=a.include_imports),ensure_ascii=False,indent=2))
