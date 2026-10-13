# 官方接口与设计参考

核对日期：2026-10-13。以下是实现依赖的公开文档，不是声称已在线运行过这些服务。

- 原项目网站：https://hongboduan.github.io/awesome-embodied-agents/
- 原项目工作流：https://raw.githubusercontent.com/hongboduan/awesome-embodied-agents/main/.github/workflows/update-and-deploy.yml
- arXiv API（日期范围、Atom、分页、id_list、版本、3 秒间隔）：https://info.arxiv.org/help/api/user-manual.html
- arXiv API 使用条款：https://info.arxiv.org/help/api/tou.html
- GitHub Actions schedule（timezone、延迟、默认分支、60 天无活动停用）：https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- GitHub Pages 自定义工作流：https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
- GITHUB_TOKEN 的事件触发边界：https://docs.github.com/en/actions/concepts/security/github_token
- checkout：https://raw.githubusercontent.com/actions/checkout/main/README.md
- setup-python：https://raw.githubusercontent.com/actions/setup-python/main/README.md
- configure-pages v5：https://raw.githubusercontent.com/actions/configure-pages/v5/action.yml
- upload-pages-artifact v4：https://raw.githubusercontent.com/actions/upload-pages-artifact/v4/action.yml
- deploy-pages：https://raw.githubusercontent.com/actions/deploy-pages/main/README.md
- Zotero Web API（分页、限流、Backoff、只读请求）：https://www.zotero.org/support/dev/web_api/v3/basics
- Zotero 题录导入：https://www.zotero.org/support/adding_items_to_zotero#importing_from_other_tools
- OpenAI Chat Completions / JSON 模式：https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create

模型端点采用 Chat Completions 兼容模式用于可选扩展；不要求选择 OpenAI，不假定所有兼容提供方都支持相同参数。基于 API 文档写出代码与完成真实账号实测是两件不同的事。
