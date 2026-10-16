# 3D Segmentation Observatory

A deployable daily arXiv discovery workflow for 3D semantic segmentation domain adaptation, domain generalization, open vocabulary, and test-time adaptation.

**Start here: [中文使用说明](README.zh-CN.md) · [部署指南](docs/DEPLOY.zh-CN.md).**

The package includes a sanitized 266-group seed index from the supplied Zotero snapshot. Daily discovery is independent of manual Zotero exports. Automatic candidates, publication permissions, and human reading status are kept separate.

Python 3.11+, standard library only. GitHub Actions + Pages. Optional read-only Zotero collection import and optional abstract-only model drafts are disabled by default.

This is an independently authored implementation inspired by the upstream project's workflow, not a fork or a drop-in patch. The repository is on GitHub and daily arXiv discovery runs in GitHub Actions (query cursors advance and data are committed every run). Publishing is the remaining step: enable Pages via Settings -> Pages -> Source: GitHub Actions, and the next run deploys the site. Optional Zotero and model API integrations remain disabled by default. See NOTICE.md and docs/LIMITS.zh-CN.md.
