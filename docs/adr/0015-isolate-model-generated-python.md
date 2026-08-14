# ADR 0015：隔离模型生成的 Python

- 状态：已接受
- 日期：2026-08-12

## 决策

`run_python` 使用确定性 `allow / ask / deny` 策略和持久化执行请求。需要审批的请求按项目、批次、会话、代码 SHA-256、能力和有效期绑定，并且只能消费一次。正式执行 Adapter 必须是经最小权限 Controller 调度的一次性 Docker 沙箱；主应用不得执行模型代码或持有 Docker socket。

沙箱只接收固定格式的代码、只读输入快照和独立输出目录。所有输出在接收进项目 `exports` 前按不可信内容验证。

## 理由

AST 检查和提示词不能构成安全边界，本地 `subprocess` 继承了主进程可见的密钥、文件和网络。分离策略、审批、执行和产物接收，使每层权限都可确定性测试和审计。

## 后果

需要维护单独镜像、Controller、清理恢复和 Docker 攻击测试。部分长尾操作会增加审批等待。迁移已经完成：正式 Adapter 只通过 Controller 使用摘要固定的一次性沙箱，缺少安全配置时 fail closed；本地 Python 启动不再回退到 `subprocess` 执行模型代码。Controller 因持有 Docker socket 被视为高权限可信边界，必须保持内部不可达、固定命令和最小职责。
