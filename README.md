# Drainage Agent

排水监测数据分析 Agent。它把监测数据读取、清洗、统计分析、降雨响应、RDII、风险评估和报告生成组织成可对话调用的工具。

## Run

```powershell
pip install -r requirements.txt
python app/agent_run.py
```

Web 版启动：

```powershell
python app/web_run.py
```

浏览器打开 `http://127.0.0.1:8000`。Web 版支持上传流量 CSV、降雨 CSV、点位信息 XLSX 和报告模板 DOCX，并复用同一套 Agent 工具。对话必须绑定当前监测项目和分析批次；会话状态与 Agent 运行摘要保存在 SQLite。工作台可按批次查看模型、工具步骤、耗时、Token、错误和产物，完整对话默认不写入运行记录。

`.env` 使用 OpenAI 兼容配置：

```env
AGENT_API_KEY=...
AGENT_BASE_URL=https://api.deepseek.com
AGENT_MODEL=deepseek-chat
```

## Docker

构建镜像：

```powershell
docker build -t drainage-agent .
```

默认启动 Web 服务。密钥在运行时通过环境变量文件注入，不会写入镜像：

```powershell
docker run --rm -p 8000:8000 --env-file .env drainage-agent
```

浏览器打开 `http://127.0.0.1:8000`。

把产物目录挂载到宿主机，容器删除后仍保留 `var/outputs/`、`var/workspace/` 和 `var/logs/`：

```powershell
docker run --rm -p 8000:8000 --env-file .env `
  -v "${PWD}/var/outputs:/app/var/outputs" `
  -v "${PWD}/var/workspace:/app/var/workspace" `
  -v "${PWD}/var/logs:/app/var/logs" `
  drainage-agent
```

镜像内已经包含脱敏演示数据 `resources/data/` 和报告模板 `resources/templates/`，所以零准备也可以跑通 demo。要使用自己的数据或模板，可以用挂载覆盖镜像内目录：

```powershell
docker run --rm -p 8000:8000 --env-file .env `
  -v "${PWD}/resources/data:/app/resources/data" `
  -v "${PWD}/resources/templates:/app/resources/templates" `
  -v "${PWD}/var/outputs:/app/var/outputs" `
  drainage-agent
```

进入 CLI 模式：

```powershell
docker run --rm -it --env-file .env `
  -v "${PWD}/var/outputs:/app/var/outputs" `
  drainage-agent python app/agent_run.py
```

## Structure

```text
analysis/              领域分析层：schema、数据读取、清洗、统计、降雨、RDII、风险、报告底料
agent/                 Agent 层：Pydantic AI 注册、CLI、工具薄封装
web/                   本地网页入口：FastAPI + 原生 HTML/CSS/JS
app/                   CLI 和 Web 启动入口
resources/data/        演示输入数据
resources/templates/   报告模板
var/outputs/           固化工具标准输出
var/workspace/         run_python 可写目录
var/logs/              运行 trace
quality/tests/         pytest 测试
quality/eval/          回归评测
docs/PROJECT_NOTES.md  项目记忆
```

## Tools

- `data_filter`
- `check_data`
- `analyze_rainfall`
- `analyze_event_response`
- `analyze_patterns`
- `analyze_rdii`
- `assess_risk`
- `generate_report`
- `list_results`
- `run_python`
- `record_note`

## Documentation

- `docs/PRD.md`: 成熟开源版本的目标规格。
- `CONTEXT.md`: 领域词汇。
- `docs/adr/`: 已接受的架构决策。
- `docs/adr/0013-keep-pydantic-ai-behind-project-aware-conversation-runner.md`: Agent 框架选型与对话运行 seam。
- `docs/EVALUATION.md`: 当前评测策略与发布门槛。
- `docs/README.md`: 文档索引及优先级。

## Tool Result Protocol

工具统一返回：

```python
{
    "status": "ok | needs_input | needs_confirmation | error",
    "summary": "...",
    "artifacts": ["var/outputs/..."],
    "data": {},
}
```

`needs_input` 用于缺少必须输入，`needs_confirmation` 用于筛选结果等待工程师确认。

## Freshness

固化工具成功后写入 `var/outputs/manifest.json`，记录输入数据指纹、参数和产物。`list_results` 会标记结果是否 fresh。

## run_python Boundary

`run_python` 以子进程执行，超时 60 秒。它注入：

- `DATA_DIR`
- `OUTPUTS_DIR`
- `WORKSPACE_DIR`
- `load_flow`
- `load_filtered_flow`
- `load_rain`
- `load_sites`

`load_flow` 只读取并规范化原始字段；`load_filtered_flow` 读取 `data_filter` 生成的有效旱天结果。`run_python` 应写入 `WORKSPACE_DIR`，固化结果由标准工具写入 `OUTPUTS_DIR`。
