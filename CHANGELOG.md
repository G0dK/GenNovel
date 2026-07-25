# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与语义化版本。

## [0.2.0] - 2026-07-26

GenNovel 的首个公开基线版本。

### 新增
- `gennovel doctor`：配置校验 + 密钥检查 + 供应商连通性逐个诊断
- `gennovel backup`：热备份（SQLite backup API，运行中亦安全）数据库/配置/提示词为 zip
- `gennovel --version`
- 服务端 API Token 鉴权（环境变量 `GENNOVEL_TOKEN`，面板自动提示输入）
- `POST /api/stop` 优雅停止：当前阶段落库后停在阶段边界，面板新增停止按钮
- 引擎文件日志 `logs/engine.log`（轮转，2MB x 3）
- 成本核算：providers 配置单价后 `status` 与面板显示估算成本
- LLM 客户端支持注入 httpx transport（离线测试）；429 遵循 `Retry-After` 头

### 变更
- CLI Ctrl-C 中断给出续跑提示并保留断点
- ruff lint 规则纳入 CI

### 基线能力
- 确定性写作流水线、滚动规划、两级节拍和举证式审校
- 修订与文本清洁、带证据状态抽取、分层记忆与全文检索
- 多供应商故障转移、断点续跑、CLI、Web 控制台与 Docker 部署
