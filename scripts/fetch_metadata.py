#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.common import load_config
from atlas.metadata import run_metadata_backfill

if __name__ == "__main__":
    raw = os.environ.get("ATLAS_METADATA_LIMIT", "").strip()
    limit = int(raw) if raw.isdigit() and int(raw) > 0 else None
    r = run_metadata_backfill(load_config(), limit=limit)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["status"] in {"ok", "disabled", "partial"} else 2)
