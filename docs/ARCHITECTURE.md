# 架构说明

本文描述当前代码的实际结构：模块各管什么、一轮对话怎么走、哪些约束由代码强制执行。设计原因见 [ADR](adr/)，评测见 [EVALUATION.md](EVALUATION.md)。

## 1. 分层

```text
浏览器工作台 (web/static/index.html)
        │ HTTP
web/        FastAPI：项目、导入、筛选确认、分析任务、文件、对话、Python 审批
        │
agent/      对话编排：Pydantic AI Agent、工具适配、会话状态、运行记录、run_python 安全链路
        │
analysis/   确定性领域计算：标准数据、筛选、统计、降雨、RDII、规律、风险、报告组装
        │
var/        SQLite 元数据 + 文件系统产物（按 项目/工作空间 隔离）
```

职责边界：LLM 只负责理解意图、选择工具、填参数、组织回复；所有数值计算在 `analysis/`，所有状态门禁在工具层或 Web 层代码中。

## 2. 目录

| 路径 | 职责 |
|---|---|
| `web/app.py` | 创建 FastAPI 应用、组装服务、公开演示限流、注册路由组 |
| `web/routes/` | 按业务分组的路由：`projects` `imports` `analysis` `files` `reports` `chat` |
| `web/schemas.py` `web/uploads.py` `web/workspace.py` `web/chat_downloads.py` | 请求模型、上传校验、工作空间重置、对话产物下载 |
| `web/projects.py` `web/standard_data.py` `web/import_profiles.py` | 项目仓储、数据导入与字段识别、导入配置 |
| `agent/core/__init__.py` | 构建 Agent：注册工具、`traced_tool` 包装、输出校验、历史压缩、确认/审批流程控制 |
| `agent/tools/tool_support.py` | 工具共用：结果缓存与 manifest、导出与工作簿、点位/时间范围判定 |
| `agent/tools/filter_tool.py` | `data_filter` 与筛选结果的人工确认 |
| `agent/tools/analysis_tools.py` | `check_data` `analyze_rainfall` `analyze_event_response` `analyze_rdii` `analyze_patterns` `assess_risk` |
| `agent/tools/report_tool.py` | `generate_report`：按章节补齐依赖结果并组装 DOCX |
| `agent/tools/python_tool.py` + `agent/python_*.py` | `run_python`：策略判定、审批记录、沙箱执行、产物校验 |
| `agent/conversations.py` | 会话加载/保存、按项目作用域构造依赖、驱动一轮对话 |
| `agent/run_records.py` | 运行与工具步骤记录（SQLite） |
| `analysis/modules/` | 纯计算：`filtering` `stats` `rainfall` `event_response` `rdii` `patterns` `dry_curves` `risk` |
| `analysis/runs.py` `analysis/jobs.py` `analysis/baselines.py` | 分析运行与结果复用、后台任务、筛选基线 |
| `analysis/reporting/` | 报告事实提取、模板契约校验、DOCX 组装 |
| `sandbox_controller/` `sandbox_runtime/` | 持有 Docker socket 的 Controller 与一次性沙箱内运行时 |
| `quality/tests/` `quality/eval/` | pytest 与 Agent Eval |

## 3. 一轮对话的执行路径

1. `POST /api/chat`（`web/routes/chat.py`）拼接附件摘录，调用 `ConversationRunner.run`。
2. `ConversationRunner`（`agent/conversations.py`）按 `session_id` 从 SQLite 读出历史和 `SessionState`，构造只指向当前项目工作空间的 `AgentDeps`，开始一条运行记录。
3. 外层包装按顺序处理流程控制：
   - `_PythonApprovalAgent`：工具要求 Python 审批时，结束本轮并返回审批请求。
   - `_FilterConfirmationAgent`：存在待确认的筛选结果时，明确确认则落定基线并续跑上一轮请求；含糊回复则追问；`data_filter` 产生新结果时结束本轮等待确认。
4. Pydantic AI 循环：系统提示词 + 历史 + 工具定义 → 模型选择工具和参数 → 执行 → 结果回填 → 直到模型给出最终回复。
5. 每次工具调用经过 `traced_tool`：检查取消 → 点位门禁 → 执行 `*_impl` → 记录参数、状态、耗时；返回 `needs_confirmation` / `needs_approval` 时抛出控制异常交给第 3 步的包装处理。
6. 最终回复经过 `output_validator`（拒绝把内部推理写进回复，触发模型重试），历史超过阈值时 `compact_history` 压缩并保留已确立的口径/点位/时间约束。
7. 保存历史与状态，结束运行记录，Web 层挑选本轮新产生的可下载文件返回。

## 4. 工具返回契约

所有工具返回统一结构（`agent/types`）：

| status | 含义 | 模型应做的事 |
|---|---|---|
| `ok` | 成功，带摘要、数据和产物路径 | 摘要关键数字 |
| `needs_input` | 缺少用户才能决定的参数，附 `options` | 按选项向用户提问，不自行填补 |
| `needs_confirmation` | 产生了需要人工确认的结果（筛选） | 由流程包装结束本轮 |
| `needs_approval` | Python 需要用户单次批准 | 由流程包装结束本轮 |
| `denied` / `failed` / `error` | 被策略拒绝或执行失败 | 告知原因并停止，不换方案兜底 |

## 5. 代码强制的门禁

| 门禁 | 位置 | 行为 |
|---|---|---|
| 点位校验 | `agent/core` `invalid_point_result` | 请求点位全部不存在时返回 `needs_input` 和有效点位 |
| 降雨场次必填 | `analysis_tools._require_event_ids` | 雨天类分析缺 `event_ids` 时返回场次选项，不允许模型编造 |
| 数据覆盖 | `analysis_tools` 覆盖检查 | 时间窗或场次无监测数据时拒绝计算并说明 |
| 筛选确认 | `filter_tool` + `_FilterConfirmationAgent` | 旱天分析只读取人工确认过的筛选基线 |
| 结果复用 | `tool_support` manifest / `analysis/runs.py` | 仅在数据指纹与参数一致时复用旧结果 |
| 报告模板契约 | `analysis/reporting` | 模板占位符或图表不完整时报告失败，不输出残缺 DOCX |
| 项目隔离 | `ConversationRunner._scoped_deps`、Web 下载路径校验 | 工具只能读写当前项目工作空间 |
| Python 执行 | 见第 6 节 | 策略、审批、沙箱、产物校验 |

## 6. run_python 安全链路

```text
模型生成代码 → AST 策略判定 allow / ask / deny (python_execution_policy)
            → 持久化请求，绑定项目/会话/代码哈希 (python_execution_requests)
            → ask：等待用户在 Web 单次批准；deny：直接拒绝
            → 经内部网络和令牌调用 Sandbox Controller
            → 一次性容器：无网络、非 root、只读根文件系统、资源配额
            → 输出文件按不可信内容校验后才进入项目目录 (python_artifacts)
```

主应用不持有 Docker socket；未配置 Controller 时 `run_python` 关闭，不回退到主进程执行。威胁模型见 [RUN_PYTHON_THREAT_MODEL.md](RUN_PYTHON_THREAT_MODEL.md)。

## 7. 状态存储

- `var/drainage.sqlite3`：项目、工作空间、导入记录、筛选基线、分析运行、后台任务、会话、运行记录、Python 执行请求。
- `var/projects/<project>/batches/<workspace>/`：`inputs/` 原始与辅助数据、`standard/` 标准化数据、`baseline/` 确认基线、`results/` 分析结果、`exports/` 可下载产物、`sessions/` 会话工作区。
- 数据替换会清空派生结果并归档会话（`web/workspace.py`）。

## 8. 已知遗留

- `_FilterConfirmationAgent` 用固定词表识别"确认继续"等明确确认语；网页另有确认按钮。
