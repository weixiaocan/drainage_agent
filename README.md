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

## Structure

```text
analysis/              领域分析层：schema、数据读取、清洗、统计、降雨、RDII、风险、报告底料
agent/                 Agent 层：Pydantic AI 注册、CLI、工具薄封装
web/                   本地网页入口：FastAPI + 原生 HTML/CSS/JS
data/                  演示输入数据
outputs/               固化工具标准输出
workspace/             run_python 可写目录
logs/                  运行 trace
PROJECT_NOTES.md       项目记忆
```

## Tools

- `query_stats`
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
- `load_rain`
- `load_sites`

`run_python` 应写入 `WORKSPACE_DIR`，固化结果由标准工具写入 `outputs/`。
