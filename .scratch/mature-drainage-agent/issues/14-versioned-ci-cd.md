# 14 — 建立版本化 CI/CD

**What to build:** 贡献者和发布者可以通过自动化流水线验证提交、构建版本化 Docker 镜像并发布开源版本。真实模型完整 Eval 只在发布前手动触发。

**Blocked by:** 13 — 完成性能、安全和持久化验证.

**Status:** ready-for-agent

- [ ] 提交和 Pull Request 自动运行 pytest、关键离线 Eval 和 Docker 构建。
- [ ] 需要真实模型和费用的完整 Eval 支持手动触发。
- [ ] 项目包含 Apache License 2.0，并检查演示材料和依赖分发许可。
- [ ] 版本标签自动生成 GitHub Release 和版本化 Docker 镜像。
- [ ] 发布门槛失败时不会发布镜像或 Release。
- [ ] 流水线结果和发布产物能够从 README 找到。
