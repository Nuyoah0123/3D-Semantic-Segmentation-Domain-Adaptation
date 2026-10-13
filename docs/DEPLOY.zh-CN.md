# 一次性部署指南

## 目标

让 GitHub 云端每天运行本项目，并把结果发布为网站。不是上传一个 ZIP 后它就会自动运行；**源码、工作流和一次性 Pages 设置都需要就位**。

此包已包含实现，尚未在你的账号创建仓库或启用定时任务。首轮务必手动运行验收。

## A. 解压并确认根目录

解压 `3d-seg-daily.zip`，进入最内层项目目录。应同时看见 `config.json`、`README.zh-CN.md`、`atlas/`、`data/`、`web/`、`scripts/`、`tests/` 和 `.github/`。

`.github` 是隐藏目录，不能遗漏。不要把整个 ZIP 当作一个文件上传；那样不会产生可执行工作流。不要把项目再嵌套一层，例如 `仓库/3d-seg-daily/.github/`；必须位于 `仓库/.github/`。

## B. 决定旧库是否同时展示

编辑 `config.json`。准备公开旧文献题录时，将下面一项设为 true：

```json
"publish_imported_references": true
```

保留 false 也能正常每日发现，新候选会按规则自动展示。初次运行前没有新候选，所以默认公开页面可能为空；包内 `_preview/index.html` 仍能查看旧库。

`publish_abstracts` 与 `publish_ai_drafts` 建议先保持 false。不要把完整 Zotero 导出、旧 Excel、私人笔记放进公开项目。

## C. 创建仓库并上传源码

在 GitHub 创建名为 `3d-seg-daily` 的新仓库，使用 main 分支。公开仓库会公开代码和所有提交的题录；私有仓库的 Pages 可用性/可见性取决于账号配置，按 GitHub 当前页面提示确认。

采用 Git 上传时，在解压后的项目目录打开终端：

```bash
git init -b main
git add .
git commit -m "Initialize daily 3D segmentation literature project"
git remote add origin https://github.com/YOUR_USERNAME/3d-seg-daily.git
git push -u origin main
```

把 `YOUR_USERNAME` 替换为自己的用户名。建议新建**空仓库**，不要再初始化第二份 README，以免出现历史冲突。第一次使用 Git 可能需要按提示配置提交身份和登录；不要把账号密码、PAT 或 API 密钥粘贴到项目文件中。

使用 GitHub Desktop 上传也可以，关键仍是目录层级正确，并保留 `.github/workflows`。`.gitignore` 已排除 `_preview/`、`dist/`、ZIP、PDF 和 Excel。

## D. 启用 Pages 与工作流权限

仓库内：

**Settings → Pages → Build and deployment → Source → GitHub Actions**。

再查看：

**Settings → Actions → General → Workflow permissions**。

工作流声明了最小的分任务权限；发现任务需要对这个仓库 `contents: write` 以保存游标，部署任务需要 Pages 写入和身份令牌。若账号/组织禁止工作流提交，按组织策略授权，或由管理员调整规则。不要为了绕过组织规则创建过度权限密钥。

本项目使用 GitHub 自带 `GITHUB_TOKEN`，不要求你另外创建写仓库的个人令牌。main 有禁止机器人直推的保护规则时，当前数据保存步骤会失败，需要采用受允许的数据分支/PR 流程；此包没有替你更改分支保护。

## E. 手动执行首次发现

打开：

**Actions → Daily papers and deploy → Run workflow → main → refresh 保持选中 → Run workflow**。

首轮查询近 30 天。成功后部署步骤会给出网站地址。之后定时运行不需要你重复上传数据。

Actions 的每一步含义：

| 步骤 | 应看到什么 |
|---|---|
| Verify pipeline | 测试通过 |
| Read selected Zotero collection | 未配置时正常显示 disabled，不是错误 |
| Discover papers and check known revisions | 每条查询 status=ok，统计新记录和修订 |
| Prepare optional abstract-only AI drafts | 默认 disabled；不需要模型密钥 |
| Check GitHub Pages configuration | Pages 已启用时成功 |
| Build using the public field allowlist | 生成 dist，不会把 private/_preview 复制进去 |
| Preserve data and source cursors | 有变化时提交 data；失败则检查权限/并发提交 |
| Publish site | 给出真实页面 URL |

若抓取失败，后续仍会尽力用保留的数据构建页面，并通过最终报告任务让工作流失败，不能只看“部署成功”就判断“发现成功”。配置错误、网络阻断或 Pages 权限错误都需要按日志处理。

## F. 每日计划如何修改

默认 `.github/workflows/daily.yml`：

```yaml
schedule:
  - cron: '35 8 * * *'
    timezone: America/Los_Angeles
```

表示洛杉矶时区每天 08:35 的计划运行时间，使用 GitHub 当前文档支持的 timezone 字段。它不是准点服务，可能延迟，极端负载下也可能丢弃排队任务。

改成自己的时间时，同时修改 `config.json` 的 `display_timezone`，使网页时间显示一致。计划文件必须在默认分支。公开仓库长期没有活动时，定时工作流可能被 GitHub 停用；定期查看 Actions，不能认为部署一次后永远无需维护。

## G. 最低验收标准

第一次：网页出现来源状态和真实运行时间；数据文件有查询游标。

再次手动运行：同一篇论文不重复收录，first_seen 不重置；出现的是 existing/unchanged，而不是全部重新计为 new。

修改人工标签：在 editorial.json 保存后重建，下一次抓取不覆盖。

禁用某条公开记录：生成 data.json、data.js、RSS、RIS 都不应再包含它。

观察至少一个后续计划运行：确定不仅能手动执行，还能被 schedule 触发。本地单元测试无法替代这一步。

## 常见问题

**没有 Actions 工作流：** 检查 `.github/workflows/daily.yml` 是否在仓库根目录下；不是 ZIP 文件内部，也不是多嵌套了一层。

**网站 404：** 检查 Pages 是否选择 GitHub Actions，deploy 是否成功，main 分支和 Pages 环境是否被保护规则阻止。不要猜测网站地址，以部署步骤显示的地址为准。

**新增为 0：** 先看全部查询是否成功。全部成功后，0 只表示这次没有首次发现符合规则的新记录，不保证领域里没有任何新论文。

**arXiv 429/503/超时：** 代码已有串行限流和有限重试。不要通过加并发或频繁点击运行解决；保留游标，按错误提示和来源恢复情况重试。

**分页到上限：** 扩大 `max_pages_per_query`、缩短日期块或拆检索式；代码不会把未处理完的窗口标为成功。

**Git push 被拒绝：** 检查 main 权限、保护规则及工作流运行期间是否有人提交；保留当前日志，处理后重新运行。不要强制 push 覆盖人工数据。

**AI 步骤失败：** 检查 JSON 模式、模型名、API 路径和 max_completion_tokens 参数支持。不能假定所有“兼容”接口完全相同。默认关闭它不影响每日抓取。
