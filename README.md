# GenNovel

[![CI](https://github.com/G0dK/GenNovel/actions/workflows/ci.yml/badge.svg)](https://github.com/G0dK/GenNovel/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
![Version](https://img.shields.io/badge/version-0.2.0-6f42c1)

GenNovel 是一个独立开发和维护的开源 AI 长篇小说生产系统。它以确定性状态机组织创作流程，
将设定、规划、写作、审校、修订、状态提取和归档纳入可追踪、可恢复的工程化管线，支持本地运行、
服务器部署以及兼容 OpenAI Chat Completions 协议的模型服务。

当前版本 `0.2.0` 是 GenNovel 的首个公开基线。

## 核心能力

- **确定性编排**：LLM 仅在语义生成节点被调用，其余流程由可测试的 Python 状态机控制。
- **滚动规划**：按故事弧、剧情单元和章节节拍逐步展开，避免一次性生成整本静态大纲。
- **一致性记忆**：章节摘要、滚动长摘要、结构化故事状态和 SQLite FTS 关键词召回协同工作。
- **质量闭环**：`draft → check → revise → deslop → extract → summarize`，支持举证式审校和多轮修订。
- **断点续跑**：每个阶段独立事务提交；进程退出后可从最后一个完成阶段继续。
- **多模型接入**：按配置顺序故障转移，支持重试、速率限制处理、Token 审计和成本估算。
- **多种操作入口**：提供 CLI、Web 控制台、HTTP API、Docker 部署和项目热备份。

## 工作流

```text
                    ┌──────────── 书籍级滚动规划 ────────────┐
premise ─▶ bible 设定集 ─▶ arcs 故事弧 ─▶ beats 章节节拍
                    └───────────────────────────────────────┘

每章：draft ─▶ check ─┬─ 通过 ─▶ deslop ─▶ extract ─▶ summarize ─▶ final
       ▲              └─ 退回 ─▶ revise ─┘
       └─────────────────────────────────┘
```

### 数据与可靠性

1. 每个流水线阶段在独立 SQLite 事务中完成并形成 checkpoint。
2. LLM 请求支持指数退避、`Retry-After` 和供应商顺序故障转移。
3. 解析失败、稿件异常缩水等情况会保留可用结果并写入可查询警告。
4. 状态变更前写入快照，运行日志默认轮转保存到 `logs/engine.log`。
5. Web 控制台支持阶段边界优雅停止，部署环境提供 `/healthz` 健康检查。
6. `gennovel backup` 使用 SQLite Backup API 生成可迁移的项目备份。

## 系统要求

- Python 3.11 或更高版本
- 可访问的兼容 OpenAI Chat Completions 协议的模型接口
- Docker 与 Docker Compose（仅容器部署需要）

## 安装

```bash
git clone https://github.com/G0dK/GenNovel.git
cd GenNovel
python -m pip install -e .
```

验证安装：

```bash
gennovel --version
```

## 快速开始

```bash
gennovel init mybook
cd mybook
```

编辑生成的 `gennovel.yaml`：

```yaml
book:
  title: "未命名作品"
  premise: "在这里填写故事前提"

providers:
  - name: primary
    base_url: "https://MODEL_HOST/v1"
    api_key_env: "MODEL_API_KEY"
    model: "MODEL_NAME"
```

设置密钥并运行：

```bash
export MODEL_API_KEY="YOUR_API_KEY"
gennovel doctor
gennovel write --chapters 1
gennovel status
gennovel export --format md
```

Windows PowerShell：

```powershell
$env:MODEL_API_KEY = "YOUR_API_KEY"
gennovel doctor
gennovel write --chapters 1
```

## 命令行

| 命令 | 说明 |
|---|---|
| `gennovel init [dir]` | 初始化书籍项目与可编辑提示词 |
| `gennovel doctor` | 校验配置、密钥和模型接口连通性 |
| `gennovel write -n N` | 推进 N 章并自动断点续跑 |
| `gennovel status` | 查看进度、警告、Token 用量与估算成本 |
| `gennovel export -f md\|txt` | 导出已完成章节 |
| `gennovel redo <章号> --from <阶段>` | 从指定阶段重新处理章节 |
| `gennovel backup` | 热备份数据库、配置和提示词 |
| `gennovel serve` | 启动 Web 控制台与 HTTP API |

所有命令均可通过 `--project/-p` 指定书籍项目目录。

## Web 控制台与 API

```bash
gennovel serve --project ./mybook --host 127.0.0.1 --port 13300
```

- Web 控制台：`http://127.0.0.1:13300`
- API 文档：`http://127.0.0.1:13300/api/docs`
- 健康检查：`http://127.0.0.1:13300/healthz`

设置 `GENNOVEL_TOKEN` 后，所有 `/api/*` 请求需携带 `X-Api-Token` 请求头或 `token` 查询参数。
面向公网部署时应同时使用 HTTPS、访问控制和反向代理。

## Docker 部署

```bash
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

首次启动会在 `./data` 中创建 `gennovel.yaml` 和提示词模板。补充故事前提、模型地址与密钥后，
即可通过 Web 控制台推进章节。

## 书籍项目结构

```text
mybook/
├── gennovel.yaml       # 书籍、模型和流水线配置
├── prompts/            # 可覆盖的提示词模板
├── logs/engine.log     # 轮转运行日志
└── book.db             # 正文、规划、状态、快照和审计数据
```

## 配置与调优

- `stages.<阶段>.provider/model`：为不同阶段分配模型。
- `providers[].price_in/price_out`：配置每百万 Token 单价，用于成本估算。
- `engine.max_revise_rounds`：设置最大修订轮数；`0` 表示跳过修订循环。
- `engine.context_budget_tokens`：设置上下文预算，超限时自动压缩长摘要。
- `book.locked_facts`：声明不可违反的世界观与剧情事实。
- `prompts/prose_rules.md`、`prompts/draft.md`：调整文风和正文生成规则。

## 开发

```bash
git clone https://github.com/G0dK/GenNovel.git
cd GenNovel
python -m pip install -e ".[dev]"
pytest -q
ruff check .
```

提交代码前请阅读：

- [贡献指南](CONTRIBUTING.md)
- [行为准则](CODE_OF_CONDUCT.md)
- [安全策略](SECURITY.md)
- [版本记录](CHANGELOG.md)

问题反馈与功能建议请使用 [GitHub Issues](https://github.com/G0dK/GenNovel/issues)，代码变更通过
[Pull Requests](https://github.com/G0dK/GenNovel/pulls) 提交。

## 项目状态与路线

`0.2.x` 阶段重点保证核心写作流水线、持久化格式和部署方式稳定。后续计划包括：

- 更完整的导入、导出与项目迁移能力；
- 可配置的人工审核节点和章节版本比较；
- 更细粒度的运行指标、模型成本与质量趋势；
- 英文提示词模板与多语言文档。

路线项目按 Issue 讨论结果推进，不承诺固定发布日期。

## License

GenNovel 使用 [MIT License](LICENSE) 发布。
