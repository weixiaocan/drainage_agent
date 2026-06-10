# 排水监测数据分析 Agent - 架构设计文档

> 版本: v0.1
> 日期: 2026-06-10
> 配套文档: PRD.md（v0.1）
> 项目形态: 独立新仓库，内含从原 Pipeline 项目复制的分析模块（只读内核）

## 文档说明

本文档回答：代码怎么组织、Agent 与既有模块怎么协作、数据怎么流转、关键技术怎么选。供 AI 协作开发工具（Claude Code 等）在每次开工前阅读，作为跨会话的全局约束。

本文档不回答：做什么功能（见 PRD.md）、具体函数怎么写（开发阶段决定）。

## 1. 设计原则

1. **内核只读**：从原项目复制来的 `pipeline/` 代码视为只读内核，Agent 层的任何需求都通过包装适配解决，不修改内核内部逻辑。唯一允许的改动是 import 路径调整和明确的 bug 修复（修复需在 commit message 中注明）。
2. **文件系统是共享状态**：模块间、工具间通过约定路径的文件传递数据（沿用原项目设计）。Agent 上下文中只流转摘要和文件路径，不流转完整数据。
3. **路径与密钥全部配置注入**：代码中不硬编码任何路径和密钥。测试时将路径指向临时目录即可运行。
4. **工具返回统一且摘要化**：所有工具返回统一结构（见 §4.2），成功时只给关键数字和产物路径，控制上下文消耗。
5. **简单优先**：不为 v2（分发）预留任何代码层面的扩展点。能用一个文件解决的不拆三个。

## 2. 顶层目录结构

```
drainage-agent/
├── agent_run.py              # CLI 入口：加载环境、初始化日志、启动对话循环
├── .env                      # 密钥 + 模型接入点（不进 git）
├── PROJECT_NOTES.md          # 项目记忆：决策结论、用户偏好、项目知识（人类可读，可手工编辑）
├── requirements.txt
├── README.md
├── agent/                    # 【新代码】Agent 层
│   ├── core.py               # Pydantic AI Agent 实例定义、模型配置、工具注册汇集
│   ├── deps.py               # 依赖注入对象定义（配置、日志、路径集合）
│   ├── cli.py                # 对话循环（输入→agent.run→输出，维护 message_history）
│   ├── tools/
│   │   ├── inspect_tools.py  # describe_data, list_results
│   │   ├── module_tools.py   # 8 个固化工具
│   │   └── python_tool.py    # run_python
│   └── prompts/
│       └── system.md         # 系统提示词（单独成文件，便于迭代）
├── pipeline/                 # 【只读内核】从原项目复制的 src/pipeline + src/core
│   ├── core/                 # 原 src/core（配置、日志、LLM客户端、工具函数）
│   └── modules/              # 原 src/pipeline 下的 8 个模块
├── data/                     # 演示数据（脱敏样例，可进 git）
├── outputs/                  # 固化工具的标准输出（结构沿用原项目）
├── workspace/                # run_python 的唯一可写目录
├── logs/                     # 对话与工具调用日志
├── tests/
└── docs/
    ├── PRD.md
    └── ARCHITECTURE.md
```

说明：

- 原项目的 `orchestrator/` 和 `run.py` **不复制**——Agent 取代了编排器的角色。原项目本身保持原样继续给同事使用，两个仓库独立演进。
- `data/` 必须是脱敏演示数据：点位名用编号代替真实位置，数据可截取真实数据的片段做时间偏移。此仓库以可公开为标准。

## 3. Agent 层设计（Pydantic AI 用法约定）

### 3.1 核心对象

- `agent/core.py` 中定义唯一的 `Agent` 实例：
  - 模型：通过 OpenAI 兼容接口接入，model / base_url / 密钥全部来自 .env 环境变量，不在代码中写死厂商（开发阶段需在 DeepSeek 与 Kimi 间实测切换）
  - `deps_type`：`agent/deps.py` 中定义的 dataclass（持有路径集合、logger、agent 环境配置、会话级项目参数），工具内通过 `RunContext.deps` 访问，这就是依赖注入的落点。不持有全局 Config——内核所需的 Config 对象由各工具包装层调用时现场组装
  - 系统提示词：启动时从 `agent/prompts/system.md` 读取
- 工具用 `@agent.tool` 装饰器注册，函数签名的类型注解即工具 schema（这是选用 Pydantic AI 的核心收益，不要再手写 JSON schema）

### 3.2 对话循环（agent/cli.py）

```
读取用户输入
→ agent.run(用户输入, deps=deps, message_history=history)
→ 打印 agent 回复
→ history = result.all_messages()
→ 回到读取输入
```

- 多轮记忆靠显式传递 `message_history`，CLI 进程内维护，不做持久化（重启即新会话，v1 接受）
- 跨会话状态分两路恢复："做过什么"从环境重建（list_results + 来历比对，见 §4.5）；"决定过什么"从项目记忆恢复（启动时将 PROJECT_NOTES.md 注入系统提示词，见 §4.6）。对话本身不持久化
- 后置参数询问与质量提醒不需要任何特殊机制：agent 的提问就是一条普通回复，回合自然交还用户，用户的回答进入下一轮输入

## 4. 工具层设计

### 4.1 固化工具的三段式结构

每个固化工具内部统一为三段，这是新增模块工具时的复制模板：

```
1. 前置检查    检查依赖的结果文件是否存在（纯文件系统判断）
2. 调用内核    用 deps 路径 + 工具参数 + 默认值组装内核所需的 Config 对象，调用 pipeline.modules.<X>.runner.run()
3. 摘要返回    从落盘结果中提取关键数字，组装统一返回结构
```

- 工具对内核是**直接 import 函数调用**（同进程），不走子进程——内核模块是可信代码
- 工具参数按"由谁决定"划分：用户维度（时间范围、点位、输出选择）+ 分析阈值（可选参数、默认值写在工具签名中，用户明确要求时才由 Agent 填写）。路径、密钥、模型等环境配置永不作为工具参数，走 deps 注入

### 4.2 统一返回结构

所有工具（含探查工具和 run_python）返回如下 dict：

```python
{
  "status": "ok" | "blocked" | "error",
  "summary": "一段给 Agent 阅读的简短结果摘要（关键数字）",
  "artifacts": ["outputs/特征曲线图/#1.png", ...],   # 产物路径，可为空
  "missing": "旱天数据筛选结果",                      # 仅 blocked 时
  "hint": "请先调用 run_data_filter",                # 仅 blocked 时
}
```

- `blocked` 是依赖恢复机制的载体：Agent 读到 missing/hint 后自主补跑前置工具
- 前置结果存在但已过期（来历早于数据更新，见 §4.5）同样按 blocked 返回，hint 提示重跑
- summary 必须包含能暴露异常的关键数字（有效/剔除天数与比例、场次数、曲线形态指标等）——这是 Agent 进行质量提醒（§4.7）的判断材料

### 4.3 工具与依赖总表

| 工具 | 内核调用 | 前置依赖（文件存在性） |
|------|----------|------------------------|
| describe_data | 无（自实现，扫描 data/） | 无 |
| list_results | 无（自实现，扫描 outputs/） | 无 |
| run_data_filter | data_filter | 无 |
| run_rainfall_analysis | rainfall_analysis | 无 |
| run_dry_analysis | dry_analysis | 筛选结果.xlsx |
| run_event_stats | event_stats | 降雨场次分析 sheet；场次编号为后置参数（对话采集后传入） |
| run_pattern_analysis | pattern_analysis | 旱天分析 sheet |
| run_rdii_analysis | rdii_analysis | 旱天分析 sheet + 降雨场次分析 sheet |
| run_risk_analysis | risk_analysis | 按内核实际依赖在包装时确认 |
| run_report_assembler | report_assembler | 拟纳入报告的各 sheet（缺哪节提示哪节） |
| run_python | 无 | 无 |
| record_note | 无（追加写 PROJECT_NOTES.md） | 无 |

- 场次选择是"后置参数"的典型：场次清单只在降雨分析完成后存在，Agent 列出清单询问用户，选定编号留在对话上下文，作为参数传给 run_event_stats / run_rdii_analysis。不存在任何文件回写

### 4.4 run_python 执行机制

- **子进程执行**：代码写入 workspace/ 下临时 .py 文件，`subprocess.run([sys.executable, tmpfile], cwd=workspace, timeout=60)` 执行。子进程崩溃、死循环不影响 Agent 主进程——这是与固化工具（同进程）做不同选择的原因：LLM 现场生成的代码不可信
- **目录约束**：执行前向代码注入只读路径常量（DATA_DIR、OUTPUTS_DIR）和可写路径（WORKSPACE_DIR）；系统提示词与工具描述中明确"只能写 workspace/"。v1 不做操作系统级隔离（PRD 已声明，沙箱加固属 v2）
- **返回**：stdout / stderr / returncode 全部返回（各截断至合理长度），报错时 Agent 依据 stderr 自行修正重试

### 4.5 结果来历与新鲜度

裸结果文件不可信——数据更新后旧结果即失效。因此结果必须携带来历：

- 固化工具落盘成功后，向 `outputs/manifest.json` 写入该结果的来历条目：生成时间、输入数据指纹、关键参数
- list_results 与前置检查比对来历与 data/ 当前状态，不一致即标记过期；过期的前置结果按 blocked 处理
- 实施节奏：阶段一只做最简版（文件修改时间比对，几行代码）；manifest 完整实现放阶段二

### 4.6 项目记忆（PROJECT_NOTES.md）

决策与项目知识无法从环境重建，必须显式记录：

- 记录质量提醒环节的确认结论、用户纠正与偏好、项目特有知识；不记录对话流水（trace 日志仅作调试）和分析结果（归 §4.5 管）
- 写入：Agent 调 record_note 工具追加；系统提示词中约定"用户给出纠正、偏好或项目知识时应记录"
- 读取：CLI 启动时整文件注入系统提示词
- 文件为人类可读 markdown，用户可直接编辑或删除条目

### 4.7 质量提醒与后置参数（取代原"介入点"机制）

无任何硬性断点字段或暂停机制——agent 没有执行惯性，对话回合制本身就是控制权交还。原 Pipeline 的介入点按性质归入两类行为：

- **后置参数**：只能在上游结果产生后选择的参数（场次编号）。由参数缺失驱动询问：Agent 发现下游工具缺少必填参数 → 跑上游分析 → 列出选项请用户选定
- **质量提醒**：系统提示词约定的判断行为——产出被下游依赖的结果后，向用户摘要 summary 中的关键数字；指标异常（剔除比例过高、曲线形态反常等）时主动指出并建议调整方向。用户声明"免确认直跑"后不再中途询问，要求"每步给我看"则步步摘要——伸缩性由对话控制，这是 agent 相对固定断点的核心优势，机制上不得锁死

## 5. 配置系统

按"由谁决定"划分，原三层配置在 Agent 模式下重组：

| 类别 | 去处 | 内容 |
|------|------|------|
| 密钥与模型接入 | .env | Agent 决策模型与内核 LLM 各一组：key、model、base_url |
| 运行常量 | 代码常量 | run_python 超时、图表 DPI 等 |
| 目录路径 | 仓库约定 + deps 集中常量 | data/、outputs/、workspace/ 为固定结构；默认值在 deps 定义一次，所有工具从 deps.paths 取，测试时构造指向临时目录的 deps |
| 分析参数 | 工具签名默认值 | 缺失率阈值、雨天判定阈值等，作为可选工具参数，对话中可临时覆盖 |
| 项目参数 | 对话收集 → 会话状态 / PROJECT_NOTES.md | 项目名、选中场次等，存于 deps 会话状态，构造内核 Config 时注入 |

- 不存在 config.yaml 与 baseinfo.xlsx：配置面只剩一个 .env，其余是代码默认值或对话输入
- 内核的 Config 类仅作为调用协议保留：工具包装层在每次调用前用上表来源组装实例，它不再是配置中心

## 6. LLM 调用

| 调用方 | 走哪里 | 重试与兜底 |
|--------|--------|-----------|
| Agent 编排决策 | Pydantic AI 的模型配置 | 框架内置重试；最终失败则报错信息进入对话，进程不退出 |
| run_python 代码生成 | 就是 Agent 决策本身（代码在工具参数里） | 同上 |
| 内核内 LLM（分类、报告文字） | 原 pipeline/core 的 LLM 客户端，原样沿用 | 沿用原有重试/兜底逻辑，不改 |

- 系统提示词唯一来源是 `agent/prompts/system.md`，包含：角色与能力边界、工具使用规则（blocked 恢复、run_python 写入约束）、质量提醒与后置参数询问的行为约定（含"免确认直跑"模式）、记忆记录时机（何时调 record_note）、回复风格（先摘要后路径）

## 7. 日志策略

- Python 标准 logging，控制台 + 文件双输出，文件按启动时间戳命名于 logs/
- Agent 层额外记录**工具调用轨迹**：每轮对话的（用户输入、工具调用序列及参数摘要、各工具 status、最终回复）以 JSONL 追加写入 logs/trace-*.jsonl——这既是调试手段，也是作品集中展示 Agent 决策过程的素材
- 接入 Langfuse 等观测平台：不在 v1 范围，JSONL 够用

## 8. 错误处理策略

Agent 模式下没有"流程终止"概念，所有失败都回到对话，由 Agent 和用户决定下一步：

| 失败类型 | 行为 |
|----------|------|
| 工具内核抛异常 | 工具内 try/except 全捕获，返回 status=error + 异常摘要，Agent 进程不崩 |
| 前置缺失 | status=blocked，Agent 自主补跑 |
| run_python 报错/超时 | stderr/超时信息返回，Agent 修正重试（系统提示词约定最多重试 2 次，仍失败则向用户说明） |
| Agent 模型 API 失败 | 框架重试后仍失败 → CLI 捕获，提示用户稍后重试，对话历史不丢失 |

## 9. 测试策略

- **工具层单测（不经 LLM）**：工具就是普通函数，直接构造 deps 调用。重点测：前置检查的 blocked 返回、摘要提取、run_python 的超时与目录约束。路径全部注入临时目录
- **Agent 层验收（经 LLM，人工）**：跑 PRD §8 的验收场景，检查 trace 日志中的工具调用序列是否符合预期
- v1 不做 LLM 行为的自动化评测（评测体系是独立的后续课题）

## 10. 关键技术决策摘要

| 决策 | 选择 | 理由 |
|------|------|------|
| Agent 框架 | Pydantic AI | 工具 schema 自动生成、参数校验内置；循环机制已通过其他项目掌握，无需重复造；OpenAI 兼容可接 DeepSeek/Kimi |
| 与原项目关系 | 独立新仓库，内核代码复制 | 原项目是同事在用的工作工具，不能当调试场；新仓库可脱敏公开作为作品集 |
| 内核调用方式 | 同进程直接 import | 可信代码，需共享 Config；子进程只会增加复杂度 |
| run_python 执行 | 子进程 + 超时 | LLM 生成代码不可信，故障必须与主进程隔离 |
| 依赖处理 | 工具前置检查 + blocked 返回 + Agent 运行时恢复 | 依赖图不集中编码，新增工具只需声明自己的前置；保留 Agent 的调度自主性 |
| 原介入点 | 取消机制：场次=后置参数采集，质量审核=提示词约定的质量提醒 | agent 无执行惯性，无需断点；硬机制会锁死"免确认直跑"的伸缩性，而伸缩性正是 agent 相对 pipeline 的优势 |
| 场次状态 | 对话上下文 + 工具参数 | 对话即状态；回写 Excel 是 Pipeline 时代的机制，Agent 模式不需要 |
| 配置体系 | "由谁决定"原则：分析参数=工具可选参数（带默认值），密钥与模型=.env，路径=仓库约定（deps 集中常量） | 对话即配置，用户可临时覆盖阈值；LLM 不应决定路径与密钥；Config 类降级为内核调用协议 |
| 记忆体系 | 结果=文件+来历元信息；决策与偏好=PROJECT_NOTES.md；对话=不持久化 | 无来历的结果在数据更新后不可信；决策无法从环境重建，必须显式记录；对话记忆易与磁盘真实状态脱节 |
| 排污规律判定逻辑迁移 | 既有 skill 中确定性指标与规则→pattern_analysis 模块代码，裁量与表述→模块内 prompt | skill 内容按"能否确定性执行"分流；skill 文档作为模块升级的实现规格 |

## 11. 开放问题（留到开发阶段）

1. Agent 决策模型实测选型：DeepSeek 与 Kimi 的工具调用可靠性需在阶段一用真实场景实测后确定
2. 内核复制后的 import 路径调整范围（原 `src.pipeline.X` → `pipeline.modules.X`），以实际复制时的报错为准逐个处理
3. describe_data 的摘要颗粒度（点位级还是文件级），以阶段一 Agent 实际决策质量反推调整
4. 各模块"摘要返回"提取哪些关键数字，包装到具体模块时逐个确定
5. 内核复制时盘点：各模块获取原 baseinfo 字段的方式——经 Config 属性的无需处理；若有直接 read_excel 该文件的，改为读 config 属性（允许的小幅内核改造，commit 注明）。同时确定每个字段做成工具参数还是开局询问

## 附录：开发顺序（与 PRD 验收阶段对应）

```
阶段一（机制验证）：
  1. 仓库初始化 + 内核复制 + import 调整 + 演示数据准备
  2. deps + 配置加载 + CLI 骨架（无工具，能对话）
  3. describe_data → list_results
  4. run_data_filter → run_dry_analysis（含前置检查与修改时间新鲜度比对），在 system.md 中调通质量提醒行为
  5. run_python
  6. 跑通 PRD 场景 A、B，调 system.md 至行为稳定

阶段二（批量接入与记忆）：
  7. 其余 6 个固化工具，按三段式模板逐个包装，每个配一条验证指令
     （pattern_analysis 以既有排污规律判定 skill 文档为规格，核对并升级模块实现）
  8. manifest 来历元信息 + record_note 项目记忆，跨会话与过期用例验证
  9. 复合场景验收 + README（架构图 + 设计说明）
```
