# Drainage Agent v1.0 发布验收报告

## 结论

截至 2026-08-10，项目已经满足首个可演示、可评估、可追溯版本的代码与评测条件。公开演示已部署到 `https://drainage.weixiaocan.com/`，可以发布 `v1.0.0`。

## 评测基线

| 层级 | 范围 | 最终结果 | 证据 |
|---|---:|---:|---|
| 确定性测试 | 全量 pytest | 279 passed | `quality/tests/` |
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

## 发布门禁状态

1. 网页端完整业务流程已经人工验收，公开 Demo 已部署。
2. 单轮、多轮和 CI Agent 冒烟证据已经保留；发布后若修改提示词、工具路由或状态管理，再复测受影响用例。
3. main 分支持续通过 `Quality Gate`；标签发布工作流会再次运行 pytest、题库校验和 Docker 构建。
4. 推送 `v1.0.0` 标签后，GitHub Actions 自动创建 Release 并发布 GHCR 镜像。

以上门禁不要求继续扩充评测题库；若标签工作流失败，应修复后重新执行对应层级，而不是跳过发布门槛。

## `run_python` 安全升级里程碑（2026-08-14）

本节是 v1.0 历史验收后的增量证据，不改写上方 2026-08-10 的版本快照。

| 层级 | 当前结果 | 证据 |
|---|---:|---|
| 全量 pytest | 368 passed，14 skipped | `quality/tests/` |
| 确定性安全 Eval | 12/12 passed | `quality/eval/run_python_security_eval.py` |
| 真实 Docker 攻击测试 | 显式启用后 13/13 passed | `quality/tests/test_docker_sandbox_attacks.py` |
| 真实 Compose 链路 | 主应用 → Controller → 摘要固定沙箱 → 产物校验通过 | `docker-compose.yml` 与运行验收记录 |

正式执行路径已经移除本地 `subprocess` 回退：主应用不持有 Docker socket；Controller 只接受认证请求、固定镜像、固定命令和受限挂载；沙箱无网络、非 root、只读根文件系统、丢弃 capabilities，并设置资源和输出配额。缺少 Controller 令牌、URL 或镜像摘要时能力 fail closed。

本次安全里程碑没有重新调用付费真实模型，也没有重新执行完整人工 Web 审批冒烟；这些质量证据沿用上方既有验收。若发布内容包含安全升级，应在候选环境补做一次短链路人工审批冒烟，并在模型、提示词或工具路由发生变化时复测受影响的真实模型 Eval，无需无差别扩充已经覆盖的单轮和多轮题库。
