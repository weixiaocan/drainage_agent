# 排水分析 Agent 设计 v2（重新设计定稿）+ 施工方案

> 取代原 docs/ARCHITECTURE.md 的工具与配置章节；其余原则（文件即状态、对话即配置、
> needs_input、来历与新鲜度、项目记忆、LLM 边界）继续有效。

---

# 第一部分：设计

## 1. 设计原则

1. 工具从用户需求长出：`data_filter` 是所有旱天筛选与清洗的唯一业务入口；旱天特征曲线降级为内部件，临时点位统计归 `run_python`
2. 数据访问层只负责文件读取、范围选择与名称归一（schema 唯一事实源），不得隐式执行另一套业务筛选
3. 原始数据与筛选数据入口显式分离：`load_flow` 返回字段已规范化的原始数据，`load_filtered_flow` 读取 `data_filter` 标准产物
4. 仅"需要人类输入"返回 needs_input（场次未选）；确定性前置一律内部补齐并在摘要注明

## 2. 数据访问层（analysis/io.py + analysis/schema.py）

**schema.py（唯一事实源）**：规范列名（时间、流量、液位、流速及单位约定）、点位命名规则、报告展示名的映射表。任何名称只在此定义一次。

**io.py**：
- `load_flow(points=全部, time_range=全部) -> DataFrame`
  - 读取流量文件并按点位、时间范围选择
  - 返回前列名/点位名归一到 schema 规范，不执行异常清洗或旱天筛选
- `load_filtered_flow(points=全部, time_range=全部) -> DataFrame`
  - 读取 `data_filter` 生成的 `var/outputs/筛选结果.xlsx`，返回有效旱天数据
- `load_rain(time_range=全部)`、`load_sites()`：同样归一后返回
- run_python 的执行环境预置 `load_flow`、`load_filtered_flow`、`load_rain`、`load_sites`

**行为约定**：`data_filter` 的有效天数必须出现在工具 summary 中，各点位日的保留/剔除原因记录在标准筛选产物中。

## 3. 工具清单（11 个）

| # | 工具 | 职责 | 参数（默认） | 来源 |
|---|------|------|--------------|------|
| 1 | data_filter | 按统一业务规则筛选有效旱天并生成标准筛选产物 | 筛选阈值使用领域默认值，可由用户明确覆盖 | 后续旱天分析的确定性基石 |
| 2 | check_data | 数据体检：收集率、缺失与异常概况、格式问题 | points=全部 | 句7 |
| 3 | analyze_rainfall | 降雨数据统计：日统计、场次划分、图表 | time_range=最近30天、output=all/daily/events/charts | 句5；只碰雨量数据 |
| 4 | analyze_event_response | 降雨事件期间各点位流量/液位/流速的指标统计（均值、峰值、响应时间） | event_ids（后置）、points=全部 | 裁决：独立工具——常在不触发风险/RDII 时单独查看 |
| 5 | analyze_patterns | 排污规律统计分析；图输出含排污规律图与旱天特征曲线图 | points=全部、output=all/table/chart | 句2；曲线图归此以保证出图格式一致 |
| 6 | analyze_rdii | 指定降雨事件下的 RDII 计算与过程线图 | event_ids（后置）、points=全部、output=all/table/chart | 句3 |
| 7 | assess_risk | 运行风险评估 | scope=all/dry/rainy；rainy 涉及场次时 event_ids 后置 | 句4；旱/雨分支为既有裁决 |
| 8 | generate_report | 双轨：默认内置模板填充；用户上传模板则按其结构自由生成 | sections=[模块列表] | 句6；裁决：模板控质量 |
| 9 | list_results | 已有结果与新鲜度清单 | 无 | 复用机制支撑件 |
| 10 | run_python | 长尾现场编程（站在数据访问层上） | code | 探讨结论 |
| 11 | record_note | 项目记忆写入 | note | 既有裁决 |

**generate_report 内部规则**：
- 对每个被选模块做可用性核实：要风险模块时检查有无雨天数据，无则只写旱天风险并在报告中注明
- 模板没有的模块走临时生成段落，附在对应位置；有模板的模块严格按模板填充
- 双轨：resources/templates/ 仅有内置模板时走默认轨（占位符填充，质量保证）；用户上传自己的 docx 后走自由生成轨——解析其章节标题结构，逐节由 LLM 基于已计算结果撰写。自由轨三条硬规则：(1) 一切数字只准引用计算结果，LLM 不得自产数值；(2) "按其格式"定义为标题级结构仿写，表格与图用本系统标准格式插入，不承诺版式复刻；(3) 摘要中声明"自由生成模式，版式细节可能与原模板有差异"
- 确定性前置内部补齐；涉及雨天内容且无已选场次时返回 needs_input 附场次清单，并说明可回复"只出旱天报告"

**内部件（不暴露为工具）**：
- 旱天特征曲线：patterns / rdii / report 的共享底料。算一次，parquet 缓存于 var/outputs/intermediate/，manifest 指纹判新鲜，过期自动重算
- 事件响应计算逻辑：analysis/ 层函数，analyze_event_response 与 assess_risk(rainy)、generate_report 共享调用

## 4. 交互与状态（沿用既有裁决，汇总备查）

- needs_input 仅两类：event_ids 未选（附场次清单）、内置模板随仓库分发不存在缺失；用户可随时上传自有模板切换到自由生成轨（Web 走上传接口，CLI 放入 resources/templates/）
- 复用：数据与参数未变 → 直接复用并注明来源；数据变 → 提示过期重跑；新参数 → 自动重跑
- 质量提醒：summary 必含可暴露异常的关键数字（剔除比例、有效天数、场次数），异常时主动提醒
- "免确认直接跑完" / "每步给我看"：伸缩由对话控制
- 项目记忆：record_note 写入 docs/PROJECT_NOTES.md，启动注入
- LLM 边界：编排决策、run_python 代码生成、排污规律分类、报告文字用 LLM；一切数值计算、清洗规则、阈值判断不用

---

# 第二部分：施工方案

## 0. 安全带（不可谈判，先做先验）

1. 用现有代码在演示数据上跑全链路，为筛选日期、数值表格和图表产物建立回归基准
2. 在对应模块测试中做精确结果比对；**先用旧代码自比对，全绿才许开工**
3. 注意：新设计有意改变了部分行为（如原始数据与筛选数据入口分离、事件响应独立化），黄金比对针对**数值核心**（筛选结果、曲线值、场次划分、RDII 值、风险值），不针对文件组织形式

## 1. 目标结构

```
analysis/                  # 纯函数库（无文件 I/O、无 Config，I/O 只在 io.py）
├── schema.py（含报告组装器共用的字段事实源）  io.py  filtering.py  rainfall.py  event_response.py
├── dry_curves.py  patterns.py  rdii.py  risk.py  reporting.py
agent/
├── core.py（11 个 @agent.tool 注册，全部带 docstring 与 RunContext 注解）
├── deps.py  types.py（needs_input 替代 blocked）  prompts/system.md
├── tools/（薄工具层，每个 ≤60 行：io 读取 → analysis 函数 → 落盘 → manifest → 摘要）
web/  quality/tests/  docs/
```

框架与决策层声明：Agent 框架沿用 Pydantic AI——工具经 @agent.tool 注册、类型注解即 schema、deps 经 RunContext[AgentDeps] 注入、模型接入走 .env（OpenAI 兼容）。本次重构不触碰决策层：core.py 的注册方式、cli.py 与 web 的对话循环、message_history 机制全部原样保留，改动范围严格限于工具层（tools/）与新建的 analysis/ 层。禁止更换或升级 agent 框架。

## 2. 施工顺序（每步：抽取 → 黄金比对绿 → 讲解关 → commit）

1. schema.py + io.py + filtering（字段规范化与业务筛选分层；筛选逻辑只保留在 data_filter）
2. data_filter + check_data（先验证筛选地基与数据体检）
3. dry_curves（内部件）+ analyze_patterns
4. analyze_rainfall
5. analyze_event_response（从原 event_stats 模块抽取计算逻辑）
6. analyze_rdii
7. assess_risk
8. generate_report（最复杂放最后；内置模板填充，必保；自主上传的内容docx 验收放宽为占位符全填充、表格图片数一致）
9. 收尾：删除 pipeline/ 目录，grep 确认无引用；trace 工具轨迹（CLI 与 Web 共用）、Web 会话隔离、fail fast（FIX_TASKS 遗留项）

## 3. 协作纪律
- 全部完成：黄金比对全绿 + pytest 全绿 + TEST_CASES 19 条复测（工具名按下表替换）

## 4. TEST_CASES 工具名对照

| 旧名 | 新名 |
|------|------|
| run_data_filter | data_filter |
| run_data_stats / describe_data | check_data |
| run_dry_analysis | （消失，= dry_curves 内部件；临时统计需求归 run_python） |
| run_rainfall_analysis | analyze_rainfall |
| run_event_stats | analyze_event_response |
| run_pattern_analysis | analyze_patterns |
| run_rdii_analysis | analyze_rdii |
| run_risk_analysis | assess_risk |
| run_report_assembler | generate_report |

