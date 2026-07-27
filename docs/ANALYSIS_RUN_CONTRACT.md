# 分析运行契约 v1

`analysis.runs.AnalysisRunner` 是 Web 和 Agent 执行确定性分析的共同入口。
第一版只迁移 `data_quality`，后续分析应通过同一接口逐项迁移，不在 Web
路由或 Agent 工具中重复实现前置条件、身份、复用、版本或落盘逻辑。

## 请求与前置条件

`AnalysisRequest` 包含监测项目、分析批次、算法、点位、起止时间和是否明确
重跑。Runner 会校验批次属于指定项目，并且只通过
`StandardDataStore.load_flow(project_id, batch_id)` 读取当前批次的 v1 标准
数据。缺少已确认标准数据时返回可操作的前置条件错误，不读取 `inputs/` 或
`resources/data`。

## 完整分析身份

每次运行保存以下身份：

- `standard_input`：标准数据契约版本和 `flow.csv` 内容 SHA-256；
- `baseline`：分析基线身份；尚无基线时固定为
  `{"kind": "none", "identity": null}`；
- `parameters`：去重排序后的点位和规范 ISO 8601 起止时间；
- `algorithm`：算法名称和显式版本。

只有完整身份一致且 `force_rerun=false` 时复用已有成功结果。明确重跑，或
标准输入、基线、规范参数、算法版本任一变化时创建新版本，并把新版本更新为
当前结果；历史产物不覆盖。

## 持久化与产物

SQLite 的 `analysis_runs` 保存运行元数据和序列化结果，
`current_analysis_results` 保存每个项目、批次和算法的当前成功结果索引。
版本化产物位于：

```text
var/projects/{project_id}/batches/{batch_id}/
└── results/{algorithm}/{run_id}/result.json
```

Web 使用
`POST /api/projects/{project_id}/batches/{batch_id}/analysis-runs/{algorithm}`；
Agent 的 `check_data` 工具在具有当前项目和批次上下文时通过
`agent.tools.analysis_runs.run_data_quality_analysis` 调用同一个 Runner。

## 本地后台作业

耗时调用通过 `analysis.jobs.BackgroundJobService` 提交完整
`AnalysisRequest`。Web 与 Agent 共用该服务；服务只负责排队、有限并发执行和
状态持久化，所有前置条件、分析身份、结果复用、版本和产物落盘仍由
`AnalysisRunner` 负责。同步 `AnalysisRunner.run()` 接口继续保留。

SQLite 的 `background_jobs` 表保存 `job_id`、项目、批次、完整请求 JSON、
状态、当前步骤、进度、错误摘要、结果 run_id/产物索引和创建、开始、完成时间。
状态为 `queued → running → succeeded|failed`。默认本地执行器最多并发执行
两个作业，可通过应用构造参数调低，但不得小于一。

进程重启不会自动重放作业：初始化服务时，遗留的 `queued` 或 `running`
统一转为 `failed`，步骤标记为“应用重启后停止”，并提示用户重新提交。
既有 `succeeded` 和 `failed` 记录保持不变。该策略避免重复执行具有外部产物
副作用的分析，也不会把未完成作业误报为成功。

## Ticket 05 集成边界

导入配置和 LLM 映射建议不得进入分析身份或 Runner。Ticket 05 只需继续生成
符合 v1 契约且经确认的标准数据；一旦标准数据内容变化，Runner 的输入内容
哈希会自然生成新的当前分析结果。
