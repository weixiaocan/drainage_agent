# `run_python` 安全执行子系统升级计划

## 1. 文档状态

- 状态：已确认，待实施
- 范围：`run_python` 的策略判定、用户审批、沙盒执行、产物接收、审计与 Docker 部署加固
- 实施方式：在新的开发对话中作为独立安全里程碑完成
- 当前行为：模型生成的 Python 仍由主应用进程通过 `subprocess` 执行；本文描述的是目标架构，不代表已经实现

## 2. 目标与安全承诺

保留 Python 代码执行这一元能力，但任何模型生成的 Python 都不能直接在主应用容器中执行。每次执行必须经过确定性策略判定；不确定或具有可理解副作用的操作必须获得用户单次批准；明确危险的操作直接拒绝；所有获准代码仍必须在一次性隔离沙盒中运行。

核心规则：

```text
最终权限 = 沙盒绝对上限 ∩ 策略允许权限 ∩ 用户本次批准权限
```

用户批准不能突破沙盒绝对上限。安全不能依赖模型遵守提示词，也不能把危险判断全部转交给用户。

## 3. 信任模型

### 3.1 信任

- 官方仓库或官方发布镜像。
- 用户主动选择并配置的模型服务商。
- Docker Engine 与宿主操作系统本身未被攻破。
- 主应用中的确定性策略与 Sandbox Controller。

### 3.2 不信任

- 模型生成的 Python 代码。
- 用户提示词、聊天附件和上传数据中的自然语言。
- Python 代码声明的目的、输入、输出和权限。
- 沙盒生成的所有输出文件。
- Agent 对风险和执行结果的自然语言解释。

### 3.3 不承诺

- 用户或其他 Agent 修改源码、安全配置或镜像后的安全性。
- 用户主动把网页暴露到公网后的访问安全。
- 用户挂载 Docker socket、宿主目录或启用特权容器后的安全性。
- 模型提供商对发送数据的处理方式。

## 4. 总体架构

```text
Agent 生成代码与能力声明
        ↓
PythonExecutionPolicy
  ├─ allow → 自动批准
  ├─ ask  → 等待用户单次审批
  └─ deny → 直接拒绝
        ↓
输入数据快照
        ↓
Sandbox Controller
        ↓
一次性 Python 沙盒
        ↓
输出文件安全校验
        ↓
接收至当前项目 exports
        ↓
运行记录与审计轨迹
```

主 Web/Agent 容器持有模型密钥、SQLite 和项目管理能力，但不得执行模型生成的代码。沙盒只获得本次代码、当前任务的只读输入快照、独立输出目录和有限临时空间，不得获得模型密钥、主数据库、应用源码、其他项目或宿主机路径。

## 5. 权限策略

### 5.1 策略结果

策略模块返回结构化决定：

```python
PolicyDecision(
    action="allow" | "ask" | "deny",
    reasons=[...],
    capabilities=[...],
    affected_paths=[...],
)
```

风险等级由确定性代码判定，不能由 Agent 自行决定。

### 5.2 自动允许

第一版仅允许：

- 通过系统函数读取当前项目、当前批次的已授权数据快照。
- 使用 pandas、NumPy、SciPy、Matplotlib、openpyxl 和 xlsxwriter 完成分析。
- 在本次独立输出目录创建新的 CSV、XLSX、JSON 和 PNG。
- 使用有限容量的 `/tmp`。
- 输出受长度限制的 stdout 和 stderr。
- 在默认 CPU、内存、时间、进程和磁盘限额内执行。

### 5.3 必须询问用户

- 覆盖当前项目已有产物。
- 删除本次任务产生的临时产物。
- 超过默认运行时间或资源等级。
- 读取当前项目中未包含在本轮默认快照内、但产品允许访问的文件。
- 产生超过默认数量或大小的输出文件。

审批必须展示执行目的、代码、代码哈希、影响文件、请求能力、资源限制和网络状态。批准只对当前项目、批次、会话、代码哈希和单次执行有效，并设置过期时间。

### 5.4 直接拒绝

- 读取环境变量、API Key、`.env` 或其他秘密。
- 访问主 SQLite、应用源码、其他项目、其他批次或宿主机文件。
- 使用绝对宿主路径、路径穿越、软链接或硬链接逃逸。
- 导入或调用 `os`、`subprocess`、`socket` 等系统能力。
- `eval`、`exec`、`compile`、动态导入和反射逃逸。
- 启动子进程、执行 shell、安装依赖、提权或监听端口。
- 访问 Docker socket或传入任意容器参数。
- 任意外部网络访问。
- 修改原始输入数据、项目配置或应用源码。
- 删除整个项目或跨项目数据。

这些能力不得通过用户审批放开。

静态检查使用 AST 和能力分析。AST 只承担风险分类，不是最终安全边界。

## 6. 执行请求与审批状态机

新增持久化执行请求，建议表名为 `python_execution_requests`，至少记录：

- `request_id`、`project_id`、`batch_id`、`session_id`、`run_id`
- 执行目的、代码、代码 SHA-256
- 策略决定、理由、请求能力、批准能力和影响路径
- 状态、创建时间、过期时间、审批时间、开始与结束时间
- stdout、stderr、退出码、错误和产物清单
- 输入快照身份和沙盒镜像摘要

状态机：

```text
requested
├─ denied
├─ approved_automatically → running
└─ awaiting_approval
   ├─ rejected
   ├─ expired
   └─ approved → running

running
├─ succeeded
├─ failed
└─ timed_out
```

强制约束：

- 代码变化后旧批准立即失效。
- 项目、批次、会话变化后旧批准失效。
- 批准只能使用一次。
- Agent 无权调用审批接口。
- `needs_approval` 返回后必须由代码强制停止本轮，不能依赖模型自觉。
- 审批后执行原请求中同一份代码，不能重新生成代码。

## 7. `run_python` 工具契约

工具从只接收代码改为接收执行契约：

```python
run_python(
    purpose: str,
    code: str,
    inputs: list[str],
    outputs: list[str],
    overwrite: bool = False,
)
```

输入使用逻辑资源名，不允许 Agent 传宿主文件路径，例如：

```text
confirmed_flow
rainfall
site_info
current_results
```

声明内容不被信任，但用于策略判定、审批展示、输入快照和审计。

## 8. PythonSandbox 模块

建立独立 seam：

```python
class PythonSandbox(Protocol):
    def execute(self, request: SandboxRequest) -> SandboxResult:
        ...
```

正式 Adapter 为 `DockerPythonSandbox`。测试使用 `FakePythonSandbox`。现有本机 `subprocess` 实现不得作为正式部署 Adapter。

沙盒执行结果只包含：

- 退出状态和退出码
- stdout、stderr
- 耗时和可获得的资源使用数据
- 候选产物列表
- 超时、资源耗尽或系统错误

## 9. 独立沙盒镜像

新增 `Dockerfile.sandbox`。镜像只包含 Python 和分析依赖，不包含 Web 应用、Agent、模型 SDK、SQLite、Git、模型密钥和项目持久化目录。

每次执行创建并销毁独立容器，运行时必须满足：

- 非 root 用户。
- 根文件系统只读。
- `network=none`。
- `cap_drop=ALL`。
- `no-new-privileges`。
- 固定 CPU、内存、进程数、运行时间、临时空间和输出空间上限。
- 不传入主应用环境变量。
- 执行结束或超时后终止整个容器并清理残留。

沙盒只能看到：

```text
/job/code/main.py   只读
/job/input/         只读
/job/output/        可写
/tmp                有容量上限
```

禁止挂载 `.env`、`/app`、`/app/var`、SQLite、其他项目、Docker socket和任意宿主目录。

## 10. Sandbox Controller

主 Web 容器不能直接获得 Docker socket。增加最小权限 Controller，其 interface 仅允许：

```text
submit(job)
status(job_id)
cancel(job_id)
```

Controller 固定镜像、命令、挂载、用户、网络和资源参数。Web 与 Agent 均不能传镜像名、宿主路径、Docker 参数、环境变量、网络模式、capabilities 或 privileged 设置。

Controller 只接受不可解释为路径的任务标识，并验证任务目录位于固定根目录。它需要处理超时、取消、重启后的孤儿容器清理和重复提交幂等性。

## 11. 输入快照与安全 Prelude

执行前创建一次性任务目录：

```text
var/sandbox-jobs/{job_id}/
├── code/main.py
├── input/
│   ├── flow.csv
│   ├── rainfall.csv
│   ├── sites.csv
│   └── manifest.json
└── output/
```

只复制本次请求所需数据。复杂输入优先标准化为 CSV 等非执行型格式。Manifest 记录项目、批次、数据身份、原文件和快照哈希、允许资源及创建时间。

Prelude 只提供：

```python
load_flow()
load_rain()
load_sites()
save_table()
save_chart()
save_json()
```

模型代码不需要知道宿主路径。保存函数统一处理文件名、扩展名、覆盖权限、文件数量和大小，并保证输出位于 `/job/output`。

## 12. 产物接收

沙盒输出不能直接进入项目目录。主应用必须逐个验证：

- 是普通文件，不是链接、设备或特殊文件。
- 规范化路径位于输出目录内。
- 扩展名属于白名单。
- 文件数量、单文件大小和总大小未超限。
- CSV、XLSX、JSON、PNG 可以正常解析。
- Excel 公式按安全策略转义或拒绝。
- 文件名重新规范化。
- 重新计算 SHA-256。

全部通过后才复制到当前项目、当前批次的 `exports`。覆盖目标时必须再次核对批准能力和路径。

## 13. Web 审批界面

当策略返回 `ask` 时，网页显示审批卡片，至少包含：

- 执行目的。
- 请求能力与影响文件。
- 风险原因。
- 时间、内存和输出限制。
- 网络是否允许，第一版固定为禁止。
- 代码哈希与完整代码查看入口。
- “允许本次”和“拒绝”操作。

默认焦点和默认按钮不得是允许。审批时重新核对项目、批次、会话、代码哈希、状态和过期时间。

## 14. Agent 硬停止

工具结果至少包括：

```text
ok
needs_approval
denied
failed
```

外层 Agent 包装器遇到 `needs_approval` 时必须立即停止后续工具调用，保存会话和请求状态，并把结构化审批信息交给 Web。拒绝后不得通过改写 Python 绕过同一安全策略；如果目标本身合理，可以建议正式分析工具或缩小请求。

## 15. 审计与可观测性

记录：

- 项目、批次、会话和运行编号。
- 执行目的、代码和代码哈希。
- 请求能力、策略决定和原因。
- 用户批准、拒绝或过期。
- 输入快照身份和哈希。
- 沙盒镜像摘要和资源限制。
- 开始、结束、退出状态、stdout 和 stderr。
- 产物路径、大小和哈希。
- 超时、取消和清理结果。

不得记录 API Key、主应用环境变量、不相关项目内容和完整输入数据。网页运行记录默认折叠代码与详细输出。

## 16. Docker 部署同步加固

该里程碑同时完成与执行链直接相关的默认部署安全：

- 网页端口默认绑定 `127.0.0.1`。
- 主应用使用非 root 用户。
- 主应用根文件系统只读。
- 持久化卷仅挂载 `/app/var`。
- 删除 capabilities，启用 `no-new-privileges`。
- 设置合理的 CPU、内存、进程和临时空间限制。
- Controller 不向宿主机开放端口。
- 主应用永远不能访问 Docker socket。
- 沙盒彻底禁用网络。
- README 明确本地部署、模型数据外发和修改后需重新审计的安全限制。

## 17. 测试计划

### 17.1 策略测试

- 安全统计自动允许。
- 覆盖产物要求审批。
- 系统模块、动态导入、绝对路径、路径穿越、环境变量、网络和反射绕过直接拒绝。
- 模型声明与实际代码不一致时以实际代码和沙盒权限为准。

### 17.2 审批状态机测试

- 未批准、拒绝或过期请求不能执行。
- 修改代码、切换项目、批次或会话后批准失效。
- 批准不能重复使用。
- `needs_approval` 后 Agent 硬停止。
- 并发和重复提交不会重复运行。

### 17.3 真实沙盒攻击测试

- 读取 `/app/.env`、主数据库和其他项目必须失败。
- 即使绕过静态策略，网络连接仍失败。
- 路径逃逸、修改只读输入、软链接和硬链接攻击失败。
- fork bomb、无限循环、大内存、磁盘填充和海量文件受到限制。
- 启动子进程、监听端口和提权失败。
- 超时、取消和 Controller 重启后无残留容器或不可控任务。

### 17.4 功能与 Eval

- 现有正常 `run_python` 单轮和多轮场景继续通过。
- 正常计算自动执行并生成正确产物。
- 灰色操作进入审批。
- 危险操作直接拒绝，并在适当时给出安全替代路径。
- 审批不会串到其他任务、会话、项目或代码版本。
- 产物只进入当前项目和批次。

## 18. 实施顺序

每个步骤独立提交，并在代码变更后运行全量 pytest：

1. 补充正式安全规格、威胁模型和 ADR。
2. 增加执行请求 Repository、数据库表与状态机。
3. 实现 `PythonExecutionPolicy` 和单元测试。
4. 建立 `PythonSandbox` interface 与 `FakePythonSandbox`。
5. 实现输入快照、安全 Prelude 和输出校验。
6. 构建独立沙盒镜像。
7. 实现最小权限 Sandbox Controller。
8. 实现 `DockerPythonSandbox` Adapter。
9. 改造 `run_python` 工具契约并接入策略与请求状态。
10. 实现 Agent 的 `needs_approval` 硬停止。
11. 增加审批接口和 Web 审批界面。
12. 完善 trace、运行记录和清理恢复。
13. 增加真实 Docker 攻击测试。
14. 迁移现有 Eval 并增加安全、灰色、危险三类场景。
15. 加固主容器和 Compose 默认配置。
16. 运行完整 pytest、离线门禁、真实模型定向 Eval、Docker 端到端测试和人工冒烟。
17. 更新 README、安全文档和发布验收结论。

## 19. 完成标准

以下条件全部满足后才可宣布升级完成：

- `run_python` 不在主应用进程或主应用容器中执行。
- 所有获准代码都在一次性沙盒中执行。
- 沙盒看不到模型密钥、主数据库、应用源码、其他项目和宿主机文件。
- `allow / ask / deny` 由确定性策略判定。
- 用户批准单次有效，并绑定项目、批次、会话和代码哈希。
- Agent 等待审批时由代码强制停止。
- 沙盒禁止网络并限制时间、CPU、内存、进程和磁盘。
- 沙盒输出经验证后才能进入项目。
- 恶意代码测试全部通过。
- Docker 默认配置采用安全限制。
- 原有正常 Python 分析能力通过测试和 Eval。

## 20. 新对话接手说明

下一对话应先阅读本文、`agent/tools/python_tool.py`、`agent/core/__init__.py`、`agent/conversations.py`、`web/app.py`、`docker-compose.yml`、`Dockerfile` 和现有 `run_python` 测试。

开始实现前先检查工作树和最近提交。第一批工作只完成安全规格、威胁模型、ADR、执行请求状态机及其测试；不要在同一个提交中同时引入 Controller、Docker 沙盒和 Web 审批，避免安全语义与基础设施问题混在一起。
