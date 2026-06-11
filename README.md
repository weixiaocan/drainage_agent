# Drainage Agent

排水监测数据分析 Agent。它把原固定顺序 Pipeline 包装成可对话调用的分析工具：用户用自然语言描述目标，Agent 自主选择探查工具、固化模块工具或 `run_python`。

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
agent/                 Agent 层：CLI、Pydantic AI agent、工具包装
web/                   本地网页入口：FastAPI + 原生 HTML/CSS/JS
pipeline/              从原项目复制的只读分析内核
data/                  演示输入数据
outputs/               固化工具标准输出
workspace/             run_python 唯一可写目录
logs/                  运行日志与 trace
PROJECT_NOTES.md       项目记忆
```

## Tools

探查工具：

- `describe_data`
- `list_results`

固化模块工具：

- `run_data_stats`
- `run_data_filter`
- `run_rainfall_analysis`
- `run_dry_analysis`
- `run_event_stats`
- `run_pattern_analysis`
- `run_rdii_analysis`
- `run_risk_analysis`
- `run_report_assembler`

长尾工具：

- `run_python`
- `record_note`

### Range Parameters

Some pipeline modules contain several useful sub-analyses, so their Agent tools expose range parameters:

- `run_rainfall_analysis(rainfall_range="all")`
  - `all` / `全部`: daily rainfall, rainfall events, and charts
  - `daily` / `降雨日`: daily rainfall summary
  - `events` / `场次`: rainfall event summary
  - `charts` / `图表`: rainfall charts
- `run_risk_analysis(scope="all")`
  - `all` / `全部`: dry-weather risk and rainy overflow risk
  - `dry` / `旱天`: dry-weather risk only
  - `rainy` / `雨天`: rainy overflow risk only

## Dependency Recovery

模块工具缺少前置结果时返回统一结构：

```python
{"status": "blocked", "missing": "...", "hint": "请先调用 ..."}
```

Agent 读到 `blocked` 后应先补跑 `hint` 指向的工具，再回到原任务。

## Freshness

固化工具成功后写入 `outputs/manifest.json`，记录输入数据指纹、参数和产物。`list_results` 与前置检查会用 manifest 标记结果是否过期。

## run_python Boundary

`run_python` 以子进程执行，超时 60 秒。它向代码注入：

- `DATA_DIR`
- `OUTPUTS_DIR`
- `WORKSPACE_DIR`

v1 仅做目录约束和提示词约束，不做操作系统级强沙箱。
