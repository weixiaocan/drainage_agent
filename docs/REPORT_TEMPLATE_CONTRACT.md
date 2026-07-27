# 报告模板契约 v1

`analysis.report_templates.ReportTemplateService` 是 Web 与 Agent 共用的报告
模板和报告草稿入口。报告只能由合规 DOCX 模板与当前批次的真实分析结果
生成，不能以任意空白 DOCX 替代。

## 必需占位符

内置和自定义模板必须包含以下全部占位符：

- `{{PROJECT_NAME}}`
- `{{BATCH_NAME}}`
- `{{ANALYSIS_SUMMARY}}`
- `{{MANUAL_TOPOLOGY_SECTION}}`

占位符可位于普通段落或表格单元格。上传时立即验证文件格式和占位符；
错误会列出缺失项。用户可以调整样式、页眉页脚、品牌元素、固定说明和
占位符所在布局，但不得删除或改名必需占位符。

## 产物与版本

每次生成都会创建不可变的新版本：

```text
var/projects/{project_id}/batches/{batch_id}/reports/{version}-{report_id}/
├── report_draft.docx
└── comprehensive_results.xlsx
```

DOCX 填充项目、批次和分析摘要。工作簿按当前分析及版本建立工作表，保留
结构化结果。空间拓扑、上下游关系等当前无法可靠自动生成的内容必须显示
为“人工补充模块”，不得伪造自动结论。新报告不覆盖历史报告。

自定义模板保存在其所属项目目录并绑定 `project_id`；跨项目模板读取和
报告生成均拒绝。报告同样严格绑定 `project_id`、`batch_id` 和模板身份。

## 接口

- Web：项目级模板上传/列表，批次级报告生成/列表和受项目隔离的文件下载。
- Agent：有项目和批次上下文时调用同一个 `ReportTemplateService`。
- 旧的通用上传接口不再接受报告模板，避免形成无项目归属的全局模板。
