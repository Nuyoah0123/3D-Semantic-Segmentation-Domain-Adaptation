#!/usr/bin/env python3
"""Apply an editorial review posted as a GitHub issue (owner-triggered, Actions only).

The website opens a prefilled issue; this script parses it, updates
data/editorial.json through the same code path as the CLI, comments the result
back on the issue and closes it. Nothing else in the pipeline can write that
file, so a human decision survives every later crawl.
"""
import json, os, re, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas.common import ROOT
from atlas.editorial import apply_review, parse_review_request

LABELS = {"candidate": "待审核", "selected": "人工精选", "metadata-checked": "题录已核对",
          "abstract-checked": "摘要已核对", "full-text-read": "人工全文阅读", "rejected": "已驳回"}
MESSAGES = {"missing-review-marker": "缺少 <!-- atlas-review --> 标记：请从网站详情页的“人工审核”按钮创建 Issue。",
            "missing-paper-id": "缺少 paper_id。",
            "unknown-paper-id": "找不到这个论文 ID（可能还没被收录）。",
            "unknown-state": "未知的审核状态。",
            "unknown-bucket": "未知的归类。",
            "unknown-track": "未知的研究方向标签。"}


def parse(body: str) -> dict:
    try:
        return parse_review_request(body)
    except ValueError as error:
        raise ValueError(MESSAGES.get(str(error), str(error))) from None


def api(method: str, url: str, token: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "3d-seg-review-bot"})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode() or "{}"
    return json.loads(raw) if raw.strip() else {}


def reply(repo: str, number: str, token: str, text: str, close: bool) -> None:
    if not (repo and number and token):
        return
    api("POST", f"https://api.github.com/repos/{repo}/issues/{number}/comments", token, {"body": text})
    if close:
        api("PATCH", f"https://api.github.com/repos/{repo}/issues/{number}", token, {"state": "closed"})


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    number = os.environ.get("ISSUE_NUMBER", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    body = os.environ.get("ISSUE_BODY", "")
    try:
        fields = parse(body)
        tracks = [t.strip().upper() for t in re.split(r"[,\s]+", fields.get("tracks", "")) if t.strip()] or None
        public = None
        if fields.get("public"):
            public = fields["public"].strip().lower() in {"yes", "true", "1", "public", "公开"}
        entry = apply_review(ROOT, fields["paper_id"], state=fields.get("state") or None, public=public,
                             tracks=tracks, bucket=fields.get("bucket") or None,
                             public_note=fields.get("note") or None)
    except Exception as error:
        key = str(error)
        message = "❌ 审核未能应用：" + MESSAGES.get(key, key)[:300]
        reply(repo, number, token, message, False)
        print(message)
        return 0
    state = str(entry.get("state", "candidate"))
    message = ("✅ 已应用审核：`{}` → **{}**\n\n```json\n{}\n```\n\n"
               "工作流会把 `data/editorial.json` 提交回仓库，站点随本次部署更新。"
               ).format(fields["paper_id"], LABELS.get(state, state), json.dumps(entry, ensure_ascii=False, indent=2))
    reply(repo, number, token, message, True)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
