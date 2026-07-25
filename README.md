# GenNovel

[![CI](https://github.com/G0dK/GenNovel/actions/workflows/ci.yml/badge.svg)](https://github.com/G0dK/GenNovel/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

GenNovel 是一个可本地运行或服务器部署的开源 AI 长篇小说生产系统。它提供滚动规划、章节生成、
一致性审校、修订、故事状态维护和断点续跑能力，并支持兼容 OpenAI Chat Completions 协议的模型接口。

## 功能

- 确定性写作流水线与 SQLite checkpoint
- 故事弧、章节单元和节拍滚动规划
- 起草、举证式审校、修订和文本清洁
- 章节摘要、长摘要、结构化故事状态与全文检索
- 多模型接口故障转移、重试、Token 与成本统计
- CLI、Web 控制台、HTTP API、Docker 和热备份

## 安装

```bash
git clone https://github.com/G0dK/GenNovel.git
cd GenNovel
python -m pip install -e .
```

GenNovel 需要 Python 3.11 或更高版本。

## 快速开始

```bash
gennovel init mybook
cd mybook
```

编辑生成的 `gennovel.yaml`，至少填写故事前提和模型配置：

```yaml
book:
  premise: "在这里填写故事前提"

providers:
  - name: primary
    base_url: "https://MODEL_HOST/v1"
    api_key_env: "MODEL_API_KEY"
    model: "MODEL_NAME"
```

```bash
export MODEL_API_KEY="YOUR_API_KEY"
gennovel doctor
gennovel write --chapters 1
gennovel status
gennovel export --format md
```

PowerShell 使用 `$env:MODEL_API_KEY = "YOUR_API_KEY"` 设置密钥。

## 常用命令

| 命令 | 说明 |
|---|---|
| `gennovel init [dir]` | 初始化书籍项目 |
| `gennovel doctor` | 检查配置、密钥和模型连通性 |
| `gennovel write -n N` | 推进 N 章 |
| `gennovel status` | 查看进度、警告和成本 |
| `gennovel export -f md\|txt` | 导出已完成章节 |
| `gennovel redo <章号> --from <阶段>` | 从指定阶段重跑章节 |
| `gennovel backup` | 热备份项目 |
| `gennovel serve` | 启动 Web 控制台与 API |

## Web 与 Docker

```bash
gennovel serve --project ./mybook --host 127.0.0.1 --port 13300
```

- Web 控制台：`http://127.0.0.1:13300`
- API 文档：`http://127.0.0.1:13300/api/docs`
- 健康检查：`http://127.0.0.1:13300/healthz`

Docker 部署：

```bash
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

服务端可设置 `GENNOVEL_TOKEN` 保护 `/api/*` 接口。公网部署还应配置 HTTPS 和网络访问控制。

## 开发

```bash
python -m pip install -e ".[dev]"
pytest -q
ruff check .
```

提交代码前请阅读 [贡献指南](CONTRIBUTING.md)。安全问题请按 [安全策略](SECURITY.md) 私密报告。

## License

[MIT](LICENSE)
