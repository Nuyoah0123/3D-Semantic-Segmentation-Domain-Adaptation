# 交付验证记录

日期：2026-10-13。

## 已完成

- Python 3.13.5 上运行 69 项 unittest，全部通过。测试使用合成 Atom 和模拟接口，不请求真实论文接口。
- JavaScript 通过 `node --check`。两份工作流 YAML 在本地解析成功；这不是 GitHub 远端工作流执行验证。
- 266 组脱敏题录；53 组核心；初始强标识没有跨组冲突。
- 默认公开构建 0 条旧题录，新候选开关开启；显式本地预览构建 266 条。
- Chromium 内存加载静态页面：核心/DG 筛选、题名搜索、详情、RIS 内容生成、会话收藏、390 像素布局通过；未捕获 JavaScript 异常。

## 尚未完成

当前环境外部 DNS 不可用，arXiv 实际请求未成功。Zotero 和模型 API 未配置真实账号。GitHub 仓库尚未创建或发布。

环境浏览器策略拒绝 `file://` 与本地 HTTP 导航；因此浏览器验证采用内存注入本地 HTML/CSS/JS，没有宣称实际文件导航或下载流程已经通过。常规浏览器的文件打开、浏览器下载、真实远端接口与 GitHub 部署仍应在你的环境验收。

没有向交付数据库填入模拟新论文，没有伪造 last_full_success，首屏状态为等待首次自动发现。

详细记录在 `validation/validation.json`、`validation/unit-tests.log` 和 `validation/browser-checks.json`。
