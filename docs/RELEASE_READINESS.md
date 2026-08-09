# Drainage Agent v1.0 发布验收报告

## 结论

截至 2026-08-09，项目已经满足首个可演示、可评估、可追溯版本的代码与评测条件。完成演示视频并确认 GitHub `Quality Gate` 通过后，可以发布 `v1.0.0`。

## 评测基线

| 层级 | 范围 | 最终结果 | 证据 |
|---|---:|---:|---|
| 确定性测试 | 全量 pytest | 277 passed | `quality/tests/` |
| 单轮 Agent Eval | 40 条 | 人工 40/40；最终客观检查 0 失败 | `quality/eval/eval_stage2/single_turn_final_summary.json` |
| 多轮 Agent Eval | 15 组 | 最终判定 15/15；无待判定项 | `quality/eval/eval_stage2/multiturn_final_summary.json` |
| CI Agent 冒烟 | CI001-CI003 | 已建立客观检查与 HTML 证据 | `quality/eval/eval_stage2/results_ci_checks.json` |
| Web 连通 | API、项目、导入、筛选、后台任务、下载 | 纳入 pytest | `quality/tests/test_web_app.py` 等 |

完整评测包含数据质量、降雨、事件响应、RDII、旱天规律、风险、报告、无效点位、无覆盖数据、损坏数据、范围修改、状态继承和长上下文等场景。真实模型输出保留 trace、工具调用、参数、状态和产物证据，主观质量由人工判定。

## 发布前审计

- `.env` 已被 `.gitignore` 排除，仓库只跟踪空值示例 `.env.example`。
- Web、CLI 和 Docker 启动方式已写入 README；密钥在运行时注入。
- 演示数据位于 `resources/data/`，README 明确标识为脱敏演示数据。
- 运行日志、项目数据、SQLite、工作区和默认输出目录均已排除，不作为发布内容提交。
- GitHub Actions 对 push/PR 执行无外部模型依赖的确定性门禁；真实模型冒烟仅手动触发并保存证据。
- 当前可观测性可以通过 `run_id` 关联模型回合、工具步骤、耗时、Token、错误和产物。

## 持续评测策略

1. 每次代码提交自动运行全量 pytest、单轮与多轮题库验证、Docker 构建。
2. 修改 Agent 提示词、工具路由或状态管理时，在线复测所有受影响用例。
3. 发布候选版本手动运行真实模型 CI 冒烟集；重大 Agent 修改重新运行完整单轮和多轮集。
4. 新发现的缺陷先增加确定性回归测试，再修复；需要模型判断的历史故障保留为 Eval 用例。
5. 人工标注以版本化总结和 HTML/JSONL 证据为准，不把下载目录中的临时 CSV 作为唯一依据。

## 发布前剩余事项

1. 录制并检查网页端完整业务演示视频。
2. 在 GitHub Actions 手动运行一次在线 Agent 冒烟任务并确认通过。
3. 确认 main 分支 `Quality Gate` 通过。
4. 由项目负责人创建并推送 `v1.0.0` 标签。

以上剩余事项不涉及继续扩充评测题库；若其中任一门禁失败，应修复后重新执行对应层级，而不是跳过发布门槛。
