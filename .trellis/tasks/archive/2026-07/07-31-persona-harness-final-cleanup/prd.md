# 清理 Persona Harness / Builtin Skill 残留并最终提交

## Goal

审计并删除旧 Persona Skill 实体化/发布方案残留，更新 Harness 与 Builtin
Skill 规范，完成质量检查、任务归档和提交。

## Requirements

1. 删除仅服务于已废弃 Persona Skill 持久化、发布确认、幂等创建或 overlay 的代码、测试和类型。
2. 保留并验证当前 Persona Harness、Builtin Skill 投影、Marketplace 普通安装和角色约束实现。
3. 更新 `.trellis/spec/`，明确 Persona 只生成运行时 hints，Builtin 使用用户优先且只读的有效 Skill 投影。
4. 归档本轮收尾任务及已完成的重复任务，不留下 active planning/in_progress 任务。
5. 提交全部相关工作区改动；不重置或覆盖无关用户改动。

## Acceptance Criteria

- [ ] `rg` 不再发现废弃 Persona publication/materialization/overlay 符号。
- [ ] 旧方案专属文件和测试被删除，当前实现文件均有引用或明确入口。
- [ ] 后端聚焦回归、ruff、前端构建和 lint 通过。
- [ ] 规格文档与代码契约一致，任务树无活动任务。
- [ ] 产生一个包含本次收尾内容的 Git commit。

## Notes

- Keep `prd.md` focused on requirements, constraints, and acceptance criteria.
- Lightweight tasks can remain PRD-only.
- For complex tasks, add `design.md` for technical design and `implement.md` for execution planning before `task.py start`.
