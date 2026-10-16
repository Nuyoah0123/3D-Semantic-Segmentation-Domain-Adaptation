#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.common import load_config
from atlas.pipeline import run_update

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover arXiv papers and rotate checks of known versions.")
    parser.add_argument("--days",type=int,help="One-off lookback; long windows are processed in configured chunks")
    parser.add_argument("--no-revisions",action="store_true")
    args = parser.parse_args()
    if args.days is not None and not 1 <= args.days <= 3650:
        parser.error("--days must be 1..3650")
    report = run_update(load_config(), days=args.days, check_revisions=not args.no_revisions)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["status"] in {"ok", "partial"} else 2)
