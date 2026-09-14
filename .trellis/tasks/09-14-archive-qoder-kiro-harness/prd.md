# 归档 .qoder 与 .kiro 编程插件 harness 工程

## Goal

把 Trellis 为 Qoder 与 Kiro Code 两个编程插件生成的 harness 目录（`.qoder/`、`.kiro/`）
从仓库工作区中归档下线，使 `trellis platforms` 不再把它们列为激活平台，
同时保留完整可回滚副本。

## Background

`trellis platforms` 当前显示 6 个激活平台：

```
Claude Code (claude-code) — .claude
Cursor (cursor) — .cursor
Codex (codex) — .codex   # 同时写 .agents/skills/
Kiro Code (kiro) — .kiro/skills
Qoder (qoder) — .qoder
Pi Agent (pi) — .pi
```

调研结论（已核实）：

- `.qoder`、`.kiro` 在 git 中**未被跟踪**（`git ls-files` 计数为 0），
  而 `.claude`/`.cursor`/`.pi`/`.agents`/`.grok` 均被跟踪。
  因此归档这两个目录不会产生 git 删除记录。
- `.trellis/.template-hashes.json` 中有 104 处 `.qoder` / `.kiro` 条目，
  它们是 `trellis update` 判定「托管文件是否被改动」的依据；
  目录移走后需同步剔除，否则后续 `trellis update` 会把它们当作缺失的托管文件重新生成。
- Trellis CLI 没有「按平台卸载」的子命令（`uninstall` 是整体移除，
  `update` 只有 force/skip/create-new 等冲突策略），因此归档必须手工完成。
- 仓库内对 `.qoder` / `.kiro` 的其他引用只出现在 `.agents/skills/trellis-meta/`
  的平台清单文档里，属于 Trellis 自身的通用说明，**不需要改动**。

## Requirements

1. 归档范围严格限定为 `.qoder/` 与 `.kiro/` 两个目录，不触碰
   `.claude/`、`.cursor/`、`.codex/`、`.pi/`、`.agents/`、`.grok/`、`.trellis/`。
2. 归档而非删除：整目录移动到 `.trellis/.backup-harness-archive-<YYYY-MM-DD>/` 下，
   保持原有目录结构，便于随时还原。
   选址理由：`.trellis/.gitignore` 已忽略 `.backup-*`，归档副本继续保持「不进 git」的现状，
   不会给仓库新增约 100 个跟踪文件。
3. 从 `.trellis/.template-hashes.json` 的 `hashes` 映射中剔除所有以
   `.qoder/` 或 `.kiro/` 开头的键，其余键与 `__version` 保持不变。
4. 归档后 `trellis platforms` 输出中不再出现 Kiro Code 与 Qoder。
5. 不修改 `.gitignore`、`AGENTS.md`、`.trellis/workflow.md` 与任何 `spec/` 文档。

## Non-Goals

- 不下线其他平台 harness（`.grok/` 虽已不在激活列表中，本次不处理）。
- 不调整 Trellis 工作流模板或 spec 文档。
- 不执行 git commit（按项目约定，提交由用户决定）。

## Risks

- **当前会话运行在 Kiro CLI 上**：`.kiro/hooks/` 与 `.kiro/agents/` 归档后，
  下一次 Kiro 会话将失去 Trellis 的 SessionStart / workflow-state 注入与
  `trellis-implement` / `trellis-check` / `trellis-research` 子代理。
  本次会话已加载的技能不受影响。这是用户明确要求的结果，非意外副作用。
- 若日后想恢复，把归档目录移回原位并重跑 `trellis update` 即可。

## Acceptance Criteria

- [x] `.qoder/` 与 `.kiro/` 在仓库根目录已不存在
      —— `Test-Path` 均为 `False`
- [x] `.trellis/.backup-harness-archive-2026-09-14/.qoder`（51 文件）与
      `.../.kiro`（53 文件）存在，与归档前基线一致
- [x] `.trellis/.template-hashes.json`：移除 104 个键，剩余 250 个
      （354 − 51 − 53 = 250），`.qoder`/`.kiro` 键数 0，`__version` 仍为 2，JSON 可解析
- [x] `trellis platforms` 输出仅剩 Claude Code / Cursor / Codex / Pi Agent
- [x] `trellis update --dry-run` 输出 35 行中无任何 `.qoder` / `.kiro` 引用，
      确认不会被重新生成
- [x] `git status --short` 无 `.qoder` / `.kiro` 删除项，归档目录未出现
      （被 `.trellis/.gitignore` 的 `.backup-*` 规则忽略）；
      本任务引入的唯一跟踪文件变更是 ` M .trellis/.template-hashes.json`
- [x] 保留目录文件数与基线一致：`.claude` 53 / `.cursor` 52 / `.codex` 8 /
      `.pi` 51 / `.agents` 46 / `.grok` 49
