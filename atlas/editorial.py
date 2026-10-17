"""Persistent editorial overrides written by a human reviewer.

Automatic discovery never touches data/editorial.json: it only reads it. Both
the CLI (scripts/review.py) and the issue-based review workflow
(scripts/apply_issue_review.py) go through apply_review, so a paper keeps its
human state across every later crawl.
"""
from __future__ import annotations

import re
from pathlib import Path

from .common import ROOT, read_json, write_json, iso, utcnow

REVIEW_STATES = ("candidate", "selected", "metadata-checked", "abstract-checked", "full-text-read", "rejected")
REVIEWED_STATES = {"metadata-checked", "abstract-checked", "full-text-read"}
BUCKETS = ("core", "related", "excluded")
TRACKS = ("DA", "DG", "OV", "TTA")


def known_papers(root=ROOT) -> dict:
    root = Path(root)
    known = {x["id"]: x for x in read_json(root / "data/seed.json", [])}
    known.update(read_json(root / "data/store.json", {"records": {}})["records"])
    return known


def apply_review(root=ROOT, paper_id="", state=None, public=None, tracks=None, bucket=None,
                 public_note=None, now=None) -> dict:
    """Apply one review decision to data/editorial.json and return the stored entry."""
    root = Path(root)
    known = known_papers(root)
    if paper_id not in known:
        raise ValueError("unknown-paper-id")
    if state and state not in REVIEW_STATES:
        raise ValueError("unknown-state")
    if bucket and bucket not in BUCKETS:
        raise ValueError("unknown-bucket")
    bad = [t for t in (tracks or []) if t and t not in TRACKS]
    if bad:
        raise ValueError("unknown-track")
    path = root / "data/editorial.json"
    data = read_json(path, {})
    entry = data.setdefault(paper_id, {})
    if state:
        entry["state"] = state
        if state in REVIEWED_STATES:
            # Reviewed status is bound to the crawled version: a later revision
            # automatically becomes review-stale on the site.
            entry["reviewed_version"] = known[paper_id].get("version")
        else:
            entry.pop("reviewed_version", None)
    if public is not None:
        entry["public"] = bool(public)
    if tracks is not None:
        entry["tracks"] = [t for t in tracks if t]
    if bucket:
        entry["bucket"] = bucket
    if public_note is not None:
        entry["public_note"] = str(public_note)
    entry["edited_at"] = iso(now or utcnow())
    write_json(path, data)
    return dict(entry)


REVIEW_MARKER = "<!-- atlas-review -->"


def parse_review_request(body) -> dict:
    """Parse the issue body produced by the website's review buttons."""
    text = str(body or "")
    if REVIEW_MARKER not in text:
        raise ValueError("missing-review-marker")
    found = {}
    for line in text.splitlines():
        match = re.match(r"^(paper_id|state|bucket|tracks|public|note)\s*[:=]\s*(.*)$", line.strip(), re.IGNORECASE)
        if match:
            found[match.group(1).lower()] = match.group(2).strip()
    if not found.get("paper_id"):
        raise ValueError("missing-paper-id")
    return found
