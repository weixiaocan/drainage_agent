# `run_python` 安全规范

## 安全不变量

模型生成的 Python 是不可信代码。正式部署不得在 Web/Agent 进程或容器中执行它。所有执行必须依次经过确定性策略、上下文绑定的单次审批（如需要）、只读输入快照、一次性隔离沙箱和不可信产物校验。

最终权限始终是沙箱绝对上限、策略许可和本次用户批准的交集。审批不能开放网络、宿主路径、环境变量、进程创建、动态执行或其他被策略拒绝的能力。

## 执行请求

每份代码按 UTF-8 计算 SHA-256，并与项目、批次、会话和运行绑定。`allow`、`ask`、`deny` 分别进入 `approved_automatically`、`awaiting_approval`、`denied`。只有已批准状态能原子地转换为 `running`，同一批准不能重复消费。

审批必须核对请求状态、有效期、项目、批次、会话和代码哈希。代码或上下文变化要求创建新请求。终态为 `succeeded`、`failed` 或 `timed_out`，并保留最小必要审计字段。

## 部署门禁

正式 Adapter 已接线为独立 Sandbox Controller 调度的一次性 Docker 沙箱；主应用不再以本地 `subprocess` 执行模型代码，并在缺少 Controller URL、至少 32 字符令牌或不可变沙箱镜像摘要时 fail closed。

部署必须使用摘要固定的专用沙箱镜像、仅内部可达的 Controller、独立共享任务卷和宿主 Docker socket 组 ID。Controller 持有 Docker socket，属于高权限可信边界，不得公开端口、接受任意镜像或命令，也不得复用为通用调度器。

修改策略、审批状态机、Controller、沙箱镜像或 prelude、挂载、资源限制、产物验证和清理恢复逻辑后，发布验收必须重新运行全量 pytest、确定性安全 Eval、显式真实 Docker 攻击测试和主应用到产物接收的 Compose 端到端链路。
