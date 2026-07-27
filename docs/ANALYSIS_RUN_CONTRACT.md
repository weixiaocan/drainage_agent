# 分析运行契约 v2

`analysis.runs.AnalysisRunner` 是 Web 与 Agent 执行确定性分析的唯一公共入口。
它负责前置条件、分析身份、结果复用、算法版本和产物落盘；调用方不得复制这些逻辑。

## 支持的分析

`AnalysisRequest` 支持：

- `data_quality`
- `patterns`
- `rainfall`
- `event_response`
- `rdii`
- `risk`

请求必须绑定 `project_id` 和 `batch_id`。可选参数包括点位、ISO 8601
起止时间、`event_ids`、风险 `scope` 和 `force_rerun`。事件响应和 RDII
必须提供 `event_ids`；缺少时抛出带 `field=event_ids` 的
`AnalysisInputRequired`，Agent 可据此返回结构化 `needs_input`。

Runner 仅从批次的已确认标准数据读取输入：

```text
var/projects/{project_id}/batches/{batch_id}/standard/
├── flow.csv
├── rainfall.csv
└── sites.csv
```

`flow.csv` 和筛选基线沿用 Ticket 07 的公共接口。降雨、事件响应、RDII
读取 `rainfall.csv`；风险分析按范围读取所需输入。Web 和 Agent 只传递
`AnalysisRequest`，不自行计算基线身份或绕过前置条件。

## 身份、版本和复用

完整分析身份包含：

- 标准输入契约版本及所用文件的 SHA-256；
- 已确认筛选基线身份；
- 规范化点位、时间范围、事件 ID 和风险范围；
- 算法名称及显式算法版本。

身份完全一致且 `force_rerun=false` 时复用已有成功结果。输入、基线、
参数或算法版本任一变化均创建新版本；历史运行和产物不覆盖。

SQLite 的 `analysis_runs` 保存不可变运行记录，`current_analysis_results`
保存项目、批次和算法的当前成功结果索引。产物位于：

```text
var/projects/{project_id}/batches/{batch_id}/results/{algorithm}/{run_id}/result.json
```

## 同步和后台接口

同步调用保留为 `AnalysisRunner.run(request)`。耗时 Web/Agent 调用通过
`analysis.jobs.BackgroundJobService.submit(request)` 提交同一个请求，
并立即获得持久化 `job_id`。后台服务只负责排队、有限并发、状态和进度；
实际分析仍调用 Runner。

作业状态机为 `queued → running → succeeded|failed`。SQLite
`background_jobs` 保存完整请求、步骤、进度、错误摘要、结果 run ID、
产物索引和时间。进程重启时不自动重放遗留 `queued/running` 作业，而是
将其转为明确的 `failed` 并提示重新提交；既有终态不变。

## 集成边界

Ticket 05 的导入画像和映射建议不属于分析身份。Ticket 09 只迁移上述核心
分析，不引入外部队列或通用工作流引擎。后续分析必须通过扩展 Runner 的
请求验证、算法版本及处理器接入，不得在 Web 或 Agent 新建第二套状态机。
