#!/usr/bin/env python3
"""Edit persistent editorial overrides; automatic discovery never overwrites this file."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from atlas.common import ROOT
from atlas.editorial import BUCKETS, REVIEW_STATES, TRACKS, apply_review

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("paper_id")
    p.add_argument("--state",choices=list(REVIEW_STATES))
    p.add_argument("--public",choices=["yes","no"])
    p.add_argument("--tracks",nargs="*",choices=list(TRACKS))
    p.add_argument("--bucket",choices=list(BUCKETS))
    p.add_argument("--public-note",help="This note may be published. Never enter private research here.")
    a=p.parse_args()
    try:
        entry=apply_review(ROOT,a.paper_id,state=a.state,
                           public=None if not a.public else a.public=="yes",
                           tracks=a.tracks,bucket=a.bucket,public_note=a.public_note)
    except ValueError as error:
        p.error(str(error))
    print(json.dumps(entry,ensure_ascii=False,indent=2))
