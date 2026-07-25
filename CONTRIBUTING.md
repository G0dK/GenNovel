# 贡献指南

感谢参与 GenNovel。提交代码前请先搜索现有 Issue；较大的功能或架构变化建议先创建 Issue，明确
使用场景、兼容性和实现方向。

## 开发环境

```bash
pip install -e ".[dev]"
pytest          # 必须全绿
ruff check .    # 必须无告警
```

## 架构约定（请勿破坏）

1. **engine.py 保持确定性**：任何新阶段/新分支都必须是纯确定性代码，LLM 调用只通过 `LLMClient.chat` 进入；不引入 `Date.now()` 式的不可重放依赖。
2. **一个阶段 = 一个事务**：新阶段的所有写库操作必须包在一个 `db.tx()` 里，保证断点续跑语义。
3. **LLM 输出不可信**：所有 JSON 输出走 `util.extract_json`；所有正文输出走 `util.strip_fences`；对长度/结构做防御性校验，失败降级为警告而不是崩溃。
4. **提示词只放模板**：改提示词改 `src/gennovel/defaults/*.md`，不要把提示词硬编码进 Python。
5. **不新增重型依赖**：LangChain、向量库、ORM 一律不收；新依赖需要在 PR 里说明为什么标准库/httpx 做不到。

## 提交规范

- 提交信息用中文或英文均可，格式 `<类型>: <摘要>`（feat/fix/docs/test/refactor/chore）
- 新功能必须带测试；bug 修复先写复现测试
- 用户可见变更同步更新 `CHANGELOG.md`

## Pull Request

- 保持变更范围聚焦，不在同一 PR 混入无关格式化或重构。
- 描述验证方法以及对配置、数据库、CLI、API 和部署方式的影响。
- CI 必须通过；维护者可能要求补充测试、文档或迁移说明。
- 提交即表示相关贡献可按本仓库的 MIT License 发布。

参与项目交流即表示同意遵守 [行为准则](CODE_OF_CONDUCT.md)。安全问题按
[安全策略](SECURITY.md) 中的私密渠道报告。
