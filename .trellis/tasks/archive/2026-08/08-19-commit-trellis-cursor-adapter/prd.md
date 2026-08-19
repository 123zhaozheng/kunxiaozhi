# 提交 Trellis Cursor 适配

## Goal

将已通过 Trellis 安装器生成的 Cursor 平台适配作为一个独立版本提交，使 Cursor 能读取项目任务、调用 Trellis 命令并向子代理注入上下文。

## Requirements

- 纳入 `.cursor/` 下本次生成的 hooks、commands、agents 和 skills。
- 纳入安装器同步更新且哈希一致的 Trellis 管理文件。
- 不修改生成文件内容，仅验证其结构、配置引用和 Python hook 语法。
- 不纳入 trace_id 任务、业务测试草稿、OpenSandbox 手册或本地私有设置。
- 本次只提交本地 Git 版本，不推送远端。

## Acceptance Criteria

- [x] `.cursor/hooks.json` 引用的脚本全部存在。
- [x] Cursor Python hooks 可通过语法编译检查。
- [x] Cursor 适配文件与 `.trellis/.template-hashes.json` 记录一致。
- [x] 提交中不包含已明确排除的其他工作区改动。
- [x] 任务完成后归档并记录开发日志。
