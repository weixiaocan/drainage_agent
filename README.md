# Drainage Agent

面向排水监测分析人员的本地 AI 数据分析应用。它将监测数据导入、字段与单位确认、旱天筛选、降雨响应、RDII、排污规律、风险评估和报告初稿组织成一个可追溯的 Web 工作流。

> 在线演示：[https://drainage.weixiaocan.com/](https://drainage.weixiaocan.com/)
>
> 公开演示仅使用脱敏示例数据，不支持上传、替换或删除数据，运行数据会定期重置。

## 它解决什么问题

排水监测分析往往需要反复整理不同来源的 CSV/XLSX、确认字段和单位、筛选有效旱天、运行多种分析并制作图表和报告。Drainage Agent 将这些步骤放在同一个项目工作台中：

1. 创建或选择监测项目。
2. 导入监测 CSV，并按需加入降雨 CSV 和点位 XLSX。
3. 由确定性规则识别字段，无法确定的映射交给用户确认。
4. 自动生成筛选结果，用户检查或修改后明确确认为分析基线。
5. 通过对话调用确定性分析工具，查看运行步骤并下载结果。

LLM 负责理解意图、选择工具和组织文字；数值计算、数据边界、项目隔离和状态门禁由代码强制执行。报告是待工程师审核的初稿，不替代专业判断。

## 当前能力

- 多监测项目隔离，SQLite 保存元数据，文件系统保存输入和产物。
- 监测、降雨和点位数据的字段识别、单位统一与人工确认。
- 筛选结果下载、修改、重新上传和明确确认。
- 数据质量、降雨场次、事件响应、RDII、旱天规律和风险分析。
- 基于内置 DOCX 契约模板生成报告初稿和综合结果工作簿。
- 分析结果身份、新鲜度和复用判断。
- 后台任务、运行状态、耗时、Token、工具步骤、错误和产物追踪。
- DeepSeek 等 OpenAI Chat Completions 兼容模型；可选配置第二个 GLM 模型。
- Docker 单容器自部署和受限公开演示模式。

网页没有报告模板上传入口，当前用户流程使用内置报告模板。代码保留项目级自定义模板 API，供后续集成或高级调用使用。

## 快速开始

### Docker Compose（推荐）

复制环境变量示例并填写至少一个模型密钥：

```powershell
Copy-Item .env.example .env
```

```env
AGENT_API_KEY=你的模型密钥
AGENT_BASE_URL=https://api.deepseek.com
AGENT_MODEL=deepseek-chat
```

启动：

```powershell
docker compose up -d --build
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。`docker-compose.yml` 将 `/app/var` 挂载到命名卷 `drainage-state`，重建容器不会清空项目；`docker compose down -v` 会删除该卷及其中的数据。

### 本地 Python

项目以 Python 3.11 为发布基线：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app/web_run.py
```

CLI 调试入口：

```powershell
python app/agent_run.py
```

## 数据与模板

- `resources/data/`：仓库随附的脱敏演示输入。
- `resources/templates/`：内置报告模板，网页生成报告时直接使用，不要求用户上传模板。
- `var/`：运行时数据库、项目文件、分析结果、工作区和日志；默认不提交 Git。

自部署版本的网页可上传：

- 一个或多个监测 CSV；
- 可选降雨 CSV；
- 可选点位信息 XLS/XLSX；
- 筛选结果修改版 XLSX；
- 对话补充附件。

公开 Demo 会禁用上传、替换、删除、项目创建和工作区重置接口。

## 模型配置

默认模型使用 OpenAI Chat Completions 兼容接口：

```env
AGENT_API_KEY=...
AGENT_BASE_URL=https://api.deepseek.com
AGENT_MODEL=deepseek-chat
```

可选第二模型只有在配置密钥后才出现在网页下拉框：

```env
GLM_API_KEY=...
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_MODEL=glm-5.2
```

模型兼容不等于质量已经验证。当前评测证据对应仓库中记录的具体模型和版本。

## 质量与评测

发布基线包括：

- 279 项 pytest 单元与集成测试；
- 40 条单轮 Agent Eval，最终人工判定 40/40；
- 15 组多轮 Agent Eval，最终人工判定 15/15；
- 真实模型 CI 冒烟和人工 Web 端到端验收；
- Docker 构建门禁。

本地确定性门禁：

```powershell
python -m pytest
python -m quality.eval.eval_stage2.run_eval quality/eval/eval_stage2/cases_single.yaml --validate-only
python -m quality.eval.eval_stage2.run_eval quality/eval/eval_stage2/cases_multiturn_v2.yaml --validate-only
docker build -t drainage-agent .
```

完整策略和证据见 [评测策略](docs/EVALUATION.md) 与 [v1.0 发布验收](docs/RELEASE_READINESS.md)。需要真实模型并产生费用的 CI 冒烟只在 GitHub Actions 中手动触发。

## 项目结构

```text
analysis/          确定性领域分析、标准数据、任务、结果和报告组装
agent/             对话编排、工具适配、提示词、会话与运行记录
web/               FastAPI 接口和原生 HTML/CSS/JavaScript 工作台
app/               Web 与 CLI 启动入口
resources/         脱敏演示数据和内置报告模板
quality/tests/     pytest 单元与集成测试
quality/eval/      Agent Eval 题库、运行器、总结和 HTML 证据
docs/              产品、契约、架构决策、评测和发布文档
var/               本地运行状态，不作为源码发布内容
```

## 安全边界

- API 密钥只通过环境变量注入，不写入镜像、数据库或运行日志。
- 完整提示词和模型回复默认不进入运行记录。
- 原始监测文件按项目隔离；分析只读取经确认的标准数据。
- 筛选确认、数据替换和删除等状态变更需要明确操作。
- 下载路径限制在当前项目空间内。
- 公开 Demo 禁用数据上传和破坏性接口，并设置请求频率及并发上限。

## 文档

- [产品规格](docs/PRD.md)
- [领域词汇](CONTEXT.md)
- [标准数据契约](docs/STANDARD_DATA_CONTRACT.md)
- [报告模板契约](docs/REPORT_TEMPLATE_CONTRACT.md)
- [评测策略](docs/EVALUATION.md)
- [性能基线](docs/PERFORMANCE.md)
- [架构决策](docs/adr/)

## License

Apache License 2.0，详见 [LICENSE](LICENSE)。
