# Drainage Agent

排水监测数据分析 Agent。它把监测数据读取、清洗、统计分析、降雨响应、RDII、风险评估和报告生成组织成可对话调用的工具。

## Run

```powershell
pip install -r requirements.txt
python agent_run.py
```

Web 版启动：

```powershell
python web_run.py
```

浏览器打开 `http://127.0.0.1:8000`。Web 版支持上传流量 CSV、降雨 CSV、点位信息 XLSX 和报告模板 DOCX，并复用同一套 Agent 工具。

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

把产物目录挂载到宿主机，容器删除后仍保留 `outputs/`、`workspace/` 和 `logs/`：

```powershell
docker run --rm -p 8000:8000 --env-file .env `
  -v "${PWD}/outputs:/app/outputs" `
  -v "${PWD}/workspace:/app/workspace" `
  -v "${PWD}/logs:/app/logs" `
  drainage-agent
```

镜像内已经包含脱敏演示数据 `data/` 和报告模板 `templates/`，所以零准备也可以跑通 demo。要使用自己的数据或模板，可以用挂载覆盖镜像内目录：

```powershell
docker run --rm -p 8000:8000 --env-file .env `
  -v "${PWD}/data:/app/data" `
  -v "${PWD}/templates:/app/templates" `
  -v "${PWD}/outputs:/app/outputs" `
  drainage-agent
```

进入 CLI 模式：

```powershell
docker run --rm -it --env-file .env `
  -v "${PWD}/outputs:/app/outputs" `
  drainage-agent python agent_run.py
```

## Structure

```text
analysis/              领域分析层：schema、数据读取、清洗、统计、降雨、RDII、风险、报告底料
agent/                 Agent 层：Pydantic AI 注册、CLI、工具薄封装
web/                   本地网页入口：FastAPI + 原生 HTML/CSS/JS
data/                  演示输入数据
outputs/               固化工具标准输出
workspace/             run_python 可写目录
logs/                  运行 trace
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

## Tool Result Protocol

工具统一返回：

```python
{
    "status": "ok | needs_input | error",
    "summary": "...",
    "artifacts": ["outputs/..."],
    "data": {},
}
```

`needs_input` 只用于缺少降雨 `event_ids`，并附带可选场次列表。

## Freshness

固化工具成功后写入 `outputs/manifest.json`，记录输入数据指纹、参数和产物。`list_results` 会标记结果是否 fresh。

## run_python Boundary

`run_python` 以子进程执行，超时 60 秒。它注入：

- `DATA_DIR`
- `OUTPUTS_DIR`
- `WORKSPACE_DIR`
- `load_flow`
- `load_filtered_flow`
- `load_rain`
- `load_sites`

`load_flow` 只读取并规范化原始字段；`load_filtered_flow` 读取 `data_filter` 生成的有效旱天结果。`run_python` 应写入 `WORKSPACE_DIR`，固化结果由标准工具写入 `outputs/`。
