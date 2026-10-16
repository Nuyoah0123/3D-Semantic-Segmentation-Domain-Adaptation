#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.common import ROOT, read_json
from atlas.pipeline import EMPTY

if __name__ == "__main__":
    s = read_json(ROOT / "data/store.json", EMPTY); r = s.get("last_run", {})
    print("## 3D paper discovery\n")
    print("Status:", r.get("status", "not-run")); print("\nLast attempt:", r.get("at", "not-run")); print("\nLast complete success:", s.get("last_full_success", "none"))
    for source in r.get("sources", []):
        print("\n-", source["source"], ":", source["status"], "; backlog days:", source.get("backlog_days", 0))
    print("\nCounts:", r.get("counts", {}))
    llm = s.get("llm_last_run", {})
    print("\nllm_last_run:", llm.get("status", "not-run"), "| attempted:", llm.get("attempted", 0),
          "| succeeded:", llm.get("succeeded", 0), "| skipped:", len(llm.get("skipped", [])), "| errors:", len(llm.get("errors", [])))
    if llm.get("note"): print("LLM note:", llm["note"])
    z = s.get("zotero_last_run", {})
    print("\nzotero_last_run:", z.get("status", "not-run"))
