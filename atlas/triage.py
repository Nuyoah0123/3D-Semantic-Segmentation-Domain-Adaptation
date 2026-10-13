"""Conservative, explainable keyword triage; NOT a full-paper or protocol review."""
from __future__ import annotations
import re

TRACKS = {
    "DA": [r"domain[ -]adaptation", r"domain[ -]adaptive", r"cross[ -]domain adaptation"],
    "DG": [r"domain[ -]generalization", r"domain[ -]generalisation", r"cross[ -]domain generalization", r"unseen domains?"],
    "OV": [r"open[ -]vocabulary", r"open[ -]vocab", r"open[ -]world", r"open[ -]set"],
    "TTA": [r"test[ -]time adapt", r"continual test[ -]time"],
}
METHODS = {
    "self-training": r"self[ -]train|pseudo[ -]?label",
    "cross-modal": r"cross[ -]modal|multi[ -]?modal",
    "distillation": r"distill",
    "vision-language": r"vision[ -]language|\bCLIP\b",
    "prototype": r"prototyp",
    "augmentation": r"augment|domain randomiz|domain randomis",
    "alignment": r"align|adversarial",
    "foundation-model": r"foundation model|\bSAM\b",
}
D3 = r"\b3[ -]?d\b|three[ -]dimensional|point[ -]cloud|\blidar\b|\blaser scan|\brgb[ -]?d\b"

def classify(title: str, abstract: str) -> dict:
    title = title or ""
    text = title + "\n" + (abstract or "")
    d3 = bool(re.search(D3, text, re.I))
    title3d = bool(re.search(D3, title, re.I))
    segment = bool(re.search(r"segment", text, re.I))
    semantic = bool(re.search(r"semantic[ -]segment", text, re.I))
    title_seg = bool(re.search(r"segment", title, re.I))
    title_sem = bool(re.search(r"semantic[ -]segment", title, re.I))
    adjacent_title = bool(re.search(r"instance[ -]segment|panoptic[ -]segment|part[ -]segment|point[ -]cloud classif|3d object detect", title, re.I))
    task = "semantic" if title_sem else "adjacent" if adjacent_title else "semantic" if semantic else "segmentation-unspecified" if segment else "other"
    methods = [k for k, pat in METHODS.items() if re.search(pat, text, re.I)]
    tracks, evidence = [], []
    for key, pats in TRACKS.items():
        for pat in pats:
            m = re.search(pat, text, re.I)
            if m:
                tracks.append(key)
                evidence.append({"label": key, "quote": text[max(0,m.start()-65):m.end()+100].strip()})
                break
    # Open-set / open-world are discovery clues, never silently equated to OV.
    if "OV" in tracks and not re.search(r"open[ -]vocab", text, re.I):
        tracks.remove("OV")
        evidence.append({"label":"OV-related", "quote":"仅有 open-set/open-world 线索，开放词汇设置待确认"})
    core = d3 and task == "semantic" and bool(tracks)
    bucket = "core" if core else "related" if d3 and (segment or tracks) else "excluded"
    clear = bool(title3d and (title_sem or title_seg) and tracks and not adjacent_title)
    return {
        "bucket": bucket, "tracks": tracks, "task": task, "methods": methods,
        "basis": "title+abstract" if abstract else "title-only",
        "certainty": "clear-keywords" if clear else "needs-review",
        "evidence": evidence,
        "note": "自动关键词初筛；标签可能来自背景或对比描述，不代表已核实任务设置、实验条件或论文贡献。",
    }
