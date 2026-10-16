# 3D Segmentation Observatory / 每日文献观察站

这不是仅供本地浏览的文献导出，而是一个可部署的持续更新项目：**GitHub Actions 每天发现 arXiv 文献 → 保守多标签初筛 → 与历史库去重 → 检查已知论文版本 → 生成网站、RSS 和 RIS → GitHub Pages 发布。**

你的既有文献是起始去重索引，新的论文不依赖你每天从 Zotero 导出。

> 交付状态：源码、本地测试、站点构建和本地预览已准备；仓库已上传到 GitHub，**每日 arXiv 发现已在 GitHub Actions 上实际运行**（检索游标持续推进，数据每日自动提交，最近一次运行 2026-10-15 状态正常）。当前尚未完成的是 **GitHub Pages 发布**：在仓库 Settings → Pages 把 Source 设为 GitHub Actions 后，下一次运行会自动构建并部署网站。可选的 Zotero / 模型 API 集成仍为关闭状态。

## 1. 你最先需要做什么

完整解压本项目。打开 `_preview/index.html` 可以立即查看本地预览，展示你的 266 组脱敏题录（53 组来自此前核心清单）。这份预览不是线上网站，也不会在你电脑关闭后继续抓取。

接着按照 **[部署到 GitHub](docs/DEPLOY.zh-CN.md)** 完成一次性发布。云端定时工作流启用后，不需要你的电脑持续开机，也不需要每天打开 Zotero。

本项目是针对你的方向独立编写的实现，借鉴原项目“静态网站＋定时发现＋人工审核”思路，**不是已经修改好的原仓库 fork，也没有复刻其全文配图、论文卡片和全部交互**。不要把这些文件直接覆盖到原仓库。详见 NOTICE.md。

## 2. 当前已经实现

| 模块 | 真实能力 |
|---|---|
| 每日发现 | arXiv 官方 Atom API；DA、DG、OV、TTA 四组可编辑检索式 |
| 日期回查 | 首轮回查 30 天；之后依据每个查询的已完成日期回查 7 天；长时间中断按 30 天块追赶 |
| 去重与版本 | arXiv 去版本编号 / DOI 强标识匹配；相同题名不直接合并；旧文献新版本不计为新论文 |
| 版本巡检 | 每次轮询最多 40 个已知 arXiv 编号；不是每天逐篇检查所有旧文献 |
| 自动分类 | 基于题名/摘要的可解释规则；DA/DG/OV/TTA 多标签，核心/相关/排除分区 |
| 状态与容错 | 每个查询独立保存游标；失败不前移，部分失败不抹掉已有库；分页不完整时报错 |
| 人工覆盖 | `data/editorial.json` 独立维护，抓取不覆盖人工标签、排除与公开设置 |
| 网页 | 检索、方向筛选、近七天新收录、近七天新版本、人工精选、本地收藏、单篇详情 |
| 输出 | RSS；全库/近七天/当前筛选结果 RIS；近七天发现摘要清单 |
| Zotero | 可选只读读取一个指定集合、添加新题录；没有写回/删除操作；默认关闭 |
| AI | 可选 Chat Completions 兼容接口，生成摘要级中文草稿，带引用片段检查和调用次数上限；默认关闭 |
| 自动部署 | GitHub Actions + Pages，定时、手动触发、代码/人工数据变更后重建 |

**尚未实现**：CVF / OpenReview / 期刊的全面每日发现、自动下载或精读全部 PDF、实验表格可靠抽取、网页在线管理后台、Zotero 双向同步、Excel 修改自动回写、原项目的卡片与配图功能。

## 3. 发布策略：题录公开与精读审核分开

默认配置：

```json
"publication": {
  "publish_imported_references": false,
  "publish_auto_candidates": true,
  "publish_abstracts": false,
  "publish_ai_drafts": false,
  "include_related": true
}
```

- `publish_imported_references=false`：尊重此前整理包的未公开状态，266 组旧题录先仅用于去重。`_preview/` 是显式本地预览例外，不部署。
- 将它改为 `true`：允许旧题录的**公开字段**显示在网站上，不会将它们标成已精读；这一步不是每天抓新论文的前提。
- 新抓到的核心/相关论文默认作为“自动发现·待审核”发布题名、作者、日期、原文链接和初筛标签；排除项不进入公开站点。
- 不复制私人批注、附件路径或原集合名称；默认不在公开页面复制摘要全文。
- `data/seed.json` 本身保留的是脱敏题录索引。如果把源代码仓库设为公开，其中题录清单也能被看见，即使网页尚未展示。**网页开关不是 GitHub 仓库权限控制。**敏感选文清单应使用满足你账号权限条件的私有代码仓库及合适的发布环境。
- 原始 `paper.zip`、旧版整理报告/Excel、个人想法、API 密钥不要放进此仓库。

## 4. 文件地图

```text
.github/workflows/daily.yml  每日发现、数据提交、构建与发布
.github/workflows/tests.yml  pull request / push 的只读测试
config.json                 站点、检索式、公开策略、可选集成
atlas/                      Python 核心实现（仅标准库）
scripts/update.py           执行发现及滚动版本检查
scripts/build.py            生成公开 dist/ 文件
scripts/review.py           修改独立人工覆盖层
scripts/sync_zotero.py       可选只读题录补充
scripts/enrich.py            可选中文摘要草稿
scripts/status.py            输出最近一次运行状态
data/seed.json              266 组脱敏初始题录（保持 Wxxxx 映射）
data/store.json             后续候选、查询游标、版本、事件、运行状态
data/editorial.json         人工覆盖层（初始为空）
web/                        网页源代码
tests/                      使用合成 Atom/模拟 API 的本地测试
_preview/                   包内本地演示；被 .gitignore 排除
docs/                       部署、数据与已知边界
```

## 5. 本地运行（不是每天必须执行）

Python 3.11+；当前交付在 Python 3.13.5 上测试。无 pip 运行依赖。

```bash
python -m unittest discover -s tests -v
python scripts/update.py
python scripts/build.py
python -m http.server 8000 --directory dist
```

执行真实抓取需要网络能连接 `export.arxiv.org`。`update.py` 部分或全部失败退出码为 2。失败时不要删除 `data/store.json`，它保存上一次游标和已有候选。

无需联网的旧库预览：

```bash
python scripts/build.py --include-imports --out _preview
```

`--include-imports` 不改公开策略，仅生成单独预览目录。默认 `build.py` 只生成允许公开的内容。

## 6. 更改人工分类和公开状态

例如给真实已有条目 `W0119` 标记方向，但不要把“分类确认”冒充“全文精读”：

```bash
python scripts/review.py W0119 --state selected --tracks DA DG --public yes
python scripts/build.py
```

这里只是演示命令，不表示交付时已经替你批准 W0119。要排除某条新候选，请将网页显示的真实 ID 传给命令：

```bash
python scripts/review.py 'arxiv:实际编号' --state rejected --public no
```

也可在 GitHub 编辑 `data/editorial.json`；提交后工作流只重建网站，不重复抓取。`public_note` 将被公开，不能写私人研究想法。人工状态可为 candidate / selected / metadata-checked / abstract-checked / full-text-read / rejected。

## 7. 每天的使用方式

网站看“近七天新收录”，筛选 DA / DG / OV。感兴趣的论文打开原文，使用 Zotero Connector 保存，或导出选中视图的 RIS 后在 Zotero 导入。RIS 是题录交换文件，不附带 PDF；反复导入整库仍可能在 Zotero 产生重复。优先导入新选中的少量论文。

**网站负责发现；Zotero 负责保管 PDF 与私人阅读；精选层只维护少量值得长期阅读的内容。**自动发现不需要等你核对完全部旧文献。

## 8. 开启可选集成

### AI 中文草稿

先阅读 `docs/LIMITS.zh-CN.md`。在 GitHub 的 Settings → Secrets and variables → Actions 设置：

- Secret `LLM_API_KEY`
- Variable `LLM_BASE_URL`：以 `/v1` 等 API 前缀结尾，脚本会追加 `/chat/completions`
- Variable `LLM_MODEL`：你账号实际可用且支持 JSON 模式与 `max_completion_tokens` 的模型名称

然后将 `llm.enabled` 设为 `true`。不提供默认模型或默认密钥，也不会自动读你的 ChatGPT 登录状态。密钥留在执行环境，不放前端。

每轮最多 5 次、按已持久化 UTC 日计数最多 10 次请求；仅处理新发现的核心文献，不自动改写旧库。计次不等于金额预算。超时请求也可能计费；中断/回滚/提交失败可能使云端预算状态不能保存，因此生产使用应另在提供方设置费用限制。

草稿默认仅留在源数据中。`publish_ai_drafts=true` 后会显示“摘要级 AI 草稿·未审核”，仍不会自动标为精选或全文精读。**公开仓库中的 data/store.json 本身也可被访问。**数字和引用片段校验不能证明语义正确。

### 只读 Zotero 题录补充

这不是必需步骤。开启后仅通过 `GET` 读取你明确指定的云端集合；不会改写 Zotero，也不会读取或下载 PDF。

将 `zotero.enabled` 设为 `true`。在 Actions Secrets 设置 `ZOTERO_API_KEY`、`ZOTERO_LIBRARY_ID`、`ZOTERO_COLLECTION_KEY`；根据个人库/群组库设置 `library_type` 为 `users` / `groups`。API key 仅授予所需库的读取权限，不授权写入或文件访问。集合选择是程序筛选，不是密钥的细粒度权限隔离。

此版本每次分页读取指定集合，进行**添加式题录补充**：已有记录不自动覆盖，删除或移出集合也不从网站删除。它不是 Zotero 完整同步引擎，不处理子集合递归；需要的条目应直接加入这个专用集合。

## 9. 设计与接口依据

核对日期：2026-10-13。详情及官方资料见 `docs/SOURCES.md`。

本项目不保证每日都能找到相关论文，也不保证覆盖整个领域。每日运行、获取新候选、人工精选、全文精读是四件不同的事。
