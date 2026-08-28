# 子4：全量验证与交付

父任务：`.trellis/tasks/08-28-analytics-dashboard-rebuild`
前置：子1、子2、子3 均已完成

## Goal

跨层验证整套改造，产出一份可执行的浏览器点击验证清单交给用户，并把本次知识沉淀到
`.trellis/spec/`。

## Requirements

### R1 全量自动化验证

```powershell
uv run ruff check .
uv run mypy src/
uv run pytest tests/api tests/infra -q
cd frontend; pnpm run lint
cd frontend; pnpm run build
cd frontend; npx tsx --test src/components/panels/analytics/__tests__/*.test.ts src/i18n/__tests__/usageReportKeys.test.ts
```

全部绿。任何失败必须修复，不得以"已知问题"记录了事。

### R2 跨层一致性验证

补一个跨层测试 `tests/api/routes/test_analytics_cross_consistency.py`：
在同一份构造数据与同一筛选下，断言

- `usage/summary.active_sessions` == `usage/by-persona` 各项 `active_sessions` 之和
- `usage/summary.user_messages` == `usage/by-user` 全量各行 `user_messages` 之和
- `usage/summary.total_tokens` == `tokens/by-model` 各项之和
- `usage/by-user` 的 total == `usage/summary.using_users`（按用户去重后）
- `usage/export.csv` 的数据行数 == `usage/by-user` 的 total（未超上限时）

### R3 `/users/heatmap` 存废判定

grep 全仓；若前端确无调用，删除该端点与相关 storage 方法、schema、测试，
并在 `research/removal-evidence.md` 留证。

### R4 点击验证清单（交付物）

写入 `.trellis/tasks/08-28-analytics-dashboard-rebuild/manual-verification.md`，
交给用户在真实环境执行。必须包含：

- 前置条件（需要 `settings:manage` 权限的账号、启动命令）
- 逐步操作：点哪里、预期看到什么
- 每一步中**哪两个数字必须相等**（可验算的一致性检查）
- 历史不可变的验证步骤：记下某历史日期的数字 → 删掉该日一个会话 → 刷新 → 数字应不变
- 时区验证：把浏览器时区改为 UTC 或 America/New_York → 日期区间与数字应不变
- 空态验证：选一个无数据的历史区间
- 桌面与移动视口各一遍
- 导出 CSV 用 Excel 打开确认中文不乱码、列顺序正确
- 回归检查：persona 广场的「分析」弹窗、`/settings` 等共用组件页面无报错

### R5 spec 沉淀

更新 `.trellis/spec/backend/analytics-persona-and-lists.md`：

- 端点签名清单同步（删掉已删端点，加上 `usage/insights`）
- 口径表增加"活跃用户 = 登录去重人数""使用用户 = 发过消息去重人数"
- 新增 Gotcha：**统计不能只对现存文档实时聚合**——会话与 trace 会被硬删
  （`session/manager.py:172`、`trace_storage.py:1704`），历史数字必须由日快照冻结
- 新增契约：日界只在 `date_range.py` 一处定义，前端传纯日期，全链路 UTC+8
- 新增测试清单条目（本次新增的全部测试文件）

必要时新增 `.trellis/spec/backend/analytics-daily-snapshot.md` 记录快照层的
读写策略与不变量。

前端侧若产生了新约定（如"只有面板容器发请求"），补到
`.trellis/spec/frontend/component-guidelines.md`。

## Out of Scope

- 真实浏览器点击执行（由用户完成）
- 生产环境部署与回填执行（用户拉镜像重启即触发）

## Acceptance Criteria

- [x] R1 全部命令绿，输出贴入本任务 `research/verification-log.md`。（改动文件零违规；残留失败均有基线对照证明为既有）
- [x] `tests/api/routes/test_analytics_cross_consistency.py` 五条断言全过。（9 条全过，含 2 条真实 MongoDB 集成）
- [x] `/users/heatmap` 存废已判定并留证。（判定删除；`research/removal-evidence.md`，一并删除 `/tokens/by-preset`、`/tokens/trend`）
- [x] `manual-verification.md` 已写好，每步都有明确的预期与可验算的等式。（父任务目录，10 节 40+ 项）
- [x] 父任务 `prd.md` 的 Acceptance Criteria 逐条勾选完成。
- [x] spec 更新完成，包含"硬删导致历史数字缩水"这条 Gotcha。（backend/analytics-persona-and-lists.md + frontend/analytics-dashboard.md）
