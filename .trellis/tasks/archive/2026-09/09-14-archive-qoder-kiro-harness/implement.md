# 执行计划：归档 .qoder / .kiro harness

归档目录：`.trellis/.backup-harness-archive-2026-09-14/`

## 归档前基线（已采集）

| 项 | 值 |
| --- | --- |
| `.qoder` 文件数 | 51 |
| `.kiro` 文件数 | 53 |
| `.template-hashes.json` 键总数 | 354 |
| 其中 `.qoder/*` 键 | 51 |
| 其中 `.kiro/*` 键 | 53 |
| 归档后应剩键数 | 250 |
| 保留目录文件数 | `.claude` 53 / `.cursor` 52 / `.codex` 8 / `.pi` 51 / `.agents` 46 / `.grok` 49 |

## 步骤

1. 创建 `.trellis/.backup-harness-archive-2026-09-14/`。
2. `Move-Item .qoder` 与 `.kiro` 整目录进入归档目录（移动而非复制，保证原位消失）。
3. 用 Python 读取 `.trellis/.template-hashes.json`，剔除 `hashes` 中所有
   以 `.qoder/` 或 `.kiro/` 开头的键，保持 `__version` 与键序不变，
   以 2 空格缩进 + UTF-8 + LF 写回（与原文件格式一致）。
4. 验证：
   - 根目录不存在 `.qoder` / `.kiro`
   - 归档目录内文件数分别为 51 / 53
   - hash 键总数 250，`.qoder`/`.kiro` 键数 0，JSON 可解析
   - `trellis platforms` 仅剩 Claude Code / Cursor / Codex / Pi Agent
   - `git status --short` 无 `.qoder` / `.kiro` 删除项、无归档目录新增项
   - 保留目录文件数与基线一致

## 回滚方式

```powershell
Move-Item .trellis\.backup-harness-archive-2026-09-14\.qoder .
Move-Item .trellis\.backup-harness-archive-2026-09-14\.kiro  .
trellis update   # 重建 template-hashes 条目
```
