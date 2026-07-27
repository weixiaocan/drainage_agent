# 标准数据契约 v1

标准数据是字段映射、类型转换、单位统一并经排水监测分析人员确认后的批次数据。批次筛选、分析、派生批次和报告工具不得读取 `inputs/` 中的原始监测文件，只能通过 `analysis.io.StandardDataStore` 读取本契约。

## 文件位置与隔离

每个监测项目和分析批次拥有独立目录：

```text
var/projects/{project_id}/batches/{batch_id}/
├── inputs/{import_id}/{original_filename}
└── standard/
    ├── manifest.json
    └── flow.csv
```

- `inputs/` 中每次上传使用新的 `import_id`，系统不提供覆盖原始文件的接口。
- `StandardDataStore(files_root).load_flow(project_id, batch_id)` 会约束解析后的路径必须位于 `files_root`，并校验 manifest 和 CSV 字段。
- 未确认、缺少文件或契约不匹配时读取器抛出 `StandardDataUnavailable`，分析不得回退读取原始文件。

## `flow.csv`

CSV 使用 UTF-8 编码，首行为以下固定顺序的字段：

| 字段 | 类型 | 单位 | 必需性 |
| --- | --- | --- | --- |
| `timestamp` | ISO 8601 时间 | 无 | 必需，且每行有效 |
| `device_id` | 字符串 | 无 | 可空 |
| `point_id` | 字符串 | 无 | 必需 |
| `flow_lps` | 数值 | `L/s` | 必需 |
| `level_m` | 数值 | `m` | 可空 |
| `velocity_mps` | 数值 | `m/s` | 可空 |

空值写为空 CSV 单元格。字段名、顺序和规范单位属于 v1 契约；调用方不得另行定义同义字段。

## `manifest.json`

导入批次的 manifest 至少包含：

```json
{
  "contract_version": 1,
  "kind": "standard_flow",
  "columns": [
    "timestamp",
    "device_id",
    "point_id",
    "flow_lps",
    "level_m",
    "velocity_mps"
  ],
  "units": {
    "flow_lps": "L/s",
    "level_m": "m",
    "velocity_mps": "m/s"
  },
  "source_import_id": "opaque import id",
  "source_sha256": "sha256 of immutable upload bytes",
  "source_encoding": "utf-8",
  "mapping": {},
  "source_units": {},
  "file": "flow.csv"
}
```

映射或单位缺失、冲突、不确定时不得写入 `standard/`。工程师确认后才生成 manifest 和 CSV；已生成的标准数据不得由同一确认操作覆盖。

派生批次沿用相同的 `contract_version`、`kind`、`columns`、`units` 和
`file`，并使用 `source_batch_ids` 记录全部来源批次、使用
`conflict_resolutions` 记录工程师的冲突选源。派生批次不伪造
`source_import_id`、原始文件哈希或字段映射。

## Ticket 03 集成

派生批次把每个来源批次当作一个 v1 标准数据集，通过
`StandardDataStore.load_flow` 读取，并将结果写回同一 v1 契约。来源缺少已确认标准数据时停止创建。派生逻辑负责来源合并和冲突选择，不解析
`inputs/`、演示 CSV 表头或重新执行单位换算，也不使用第二套批次文件格式。
