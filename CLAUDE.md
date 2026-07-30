# CLAUDE.md

排水监测数据分析 Agent（Drainage Agent）：本地自部署的 Web 应用，面向排水监测分析人员。
核心形态 = 左侧工作台（确定性状态）+ 右侧对话（Agent 意图编排）。FastAPI + 原生 HTML/JS 单页（`web/static/index.html`），SQLite 存元数据，文件系统按项目/批次隔离，Docker 单容器部署。

## 工作规则（源自 AGENTS.MD，必须遵守）

1. 每次代码改动后先跑 `python -m pytest` 再交付；跑不了要明确报告命令和失败原因。
2. 验证通过后提交 git 并推送 GitHub；推送失败要报告具体命令和失败信息。
3. 每次重构/新增后检查遗留死代码和与项目文档的不一致，发现后告知用户并给出建议，不要擅自扩大范围。

## 分层与数据流

```text
analysis/   领域分析层：io/standard 契约、筛选基线、数据质量、降雨、事件响应、RDII、风险、报告
agent/      Agent 层：Pydantic AI、对话运行（conversations.py）、工具薄封装（tools/）、运行记录
web/        FastAPI 入口：app.py 全部路由；projects/standard_data/import_profiles 模块
quality/    pytest 测试（tests/）与回归评测（eval/）
var/        运行时数据：drainage.sqlite3 + projects/{pid}/batches/{bid}/（git 忽略，勿提交）
.scratch/mature-drainage-agent/issues/  15 张任务票（本地拆分记录）
```

主流程：监测项目 → 分析批次 → 导入 CSV（智能识别为主、标准模板备用）→ 标准数据 →
筛选确认（分析基线）→ 对话分析/报告。侧边栏按此 5 步组织，低频功能一律收折叠区。

## 领域硬约束（改动不得违反）

- **LLM 只建议、不决定**：映射候选、筛选结果、报告初稿都必须经工程师明确确认才生效；
  禁止根据数值猜测单位或字段含义。
- **标准数据契约 v1**：分析/筛选/报告工具只能读 `batches/{bid}/standard/`（见
  `docs/STANDARD_DATA_CONTRACT.md`），不得读 `inputs/` 原始文件；标准数据生成后不可覆盖。
- **确定性优先**：字段识别先走确定性规则（`BatchDataImporter.COLUMN_RULES`），
  LLM 只在规则失败时提候选；分析数值必须来自确定性计算，LLM 只可组织文字。
- **不可变输入**：原始监测文件只增不改；新数据 = 新批次，不追加、不去重、不覆盖旧批次。
- **安全**：API 密钥不落盘不记日志；完整提示词/回复默认不记录（深度调试需用户显式开启）；
  文件下载限制在项目/批次空间内，防路径穿越。

## 关键设计决策（避免重复讨论）

- **派生分析批次已移除**（2026-07）：跨批次合并没有真实场景，需要某时段数据就全量导出建新批次。
  不要再引入合并类功能；PRD 第 9、10 条已划线标注，ADR 0002 有更新注记。
- **导入交互**：全认准的列零确认直接导入；认不准的列才逐个问用户（中文选项）；
  AI 猜测必须标注待确认；"保存映射配置"只在导入成功后作为可选项出现。
- **LLM 接入**：`.env` 用 OpenAI 兼容配置（`AGENT_*` 或 `DEEPSEEK_*`）。映射候选走
  `LLMMappingSuggester`，create_app 按 api_key 有无自动接线（无 key 回退
  `NoMappingSuggester` 桩，测试用）。
- **旧全局上传** `/api/upload`（流量/降雨/点位信息 → `resources/data/`）仍在服务旧版
  Agent 工具（降雨场次等），属已知过渡态；界面只保留降雨+点位信息入口（折叠区"辅助数据"）。

## 常用命令

```bash
python -m pytest            # 全量测试（质量门槛：必须全绿）
python app/web_run.py       # 本地 Web（http://127.0.0.1:8000）
docker compose up -d --build  # 容器化运行，var/ 挂命名卷
```

测试约定：真实临时 SQLite + 临时文件目录；Agent 用 `FakeAgent` 替身；LLM 调用一律替身或桩，不产生费用。

## 当前进度（2026-07）

- `.scratch` 15 张任务票：1–13 完成；14（版本化 CI/CD）、15（公开 Demo 与发布）未做。
- 已完成界面重组（5 步工作流）、派生批次移除、导入智能识别 + 模板路径、真实 LLM 映射候选接入。
- 下一步：按用户界面测试反馈修问题，然后做 ticket 14。

## 文档指引

- `docs/PRD.md` 产品规格；`CONTEXT.md` 领域词汇；`docs/adr/` 架构决策（改架构前先读相关 ADR）；
  `docs/README.md` 文档索引。改功能时同步更新相关文档，保持代码与文档一致。
