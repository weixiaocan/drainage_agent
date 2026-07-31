# 项目文档

## 当前权威文档

- `PRD.md`：成熟开源版本的产品规格、用户故事、范围和验收要求。
- `../CONTEXT.md`：排水监测分析领域的统一业务语言。
- `adr/`：已经确认且需要长期保留原因的架构决策。
- `ARCHITECTURE_AUDIT.md`：现有代码到目标架构的模块、接缝与增量重构方案。
- `EVALUATION.md`：当前评测策略和发布门槛。
- `STANDARD_DATA_CONTRACT.md`：批次标准流量数据的 v1 文件格式、manifest 和公开读取契约。
- `../.scratch/mature-drainage-agent/issues/`：按依赖顺序执行的本地实施票据。

## 运行文档

- `../README.md`：当前版本的安装、运行和工具说明。

## 历史材料

- `history/EVAL_V2_RETROSPECTIVE.md`：主体功能开发阶段的评测过程与经验总结，不代表当前发布基线。

发生冲突时，领域词汇和已接受的 ADR 优先于 PRD；PRD 优先于历史材料。README 应描述当前已实现行为，不能把 PRD 中尚未实现的目标写成现有能力。
