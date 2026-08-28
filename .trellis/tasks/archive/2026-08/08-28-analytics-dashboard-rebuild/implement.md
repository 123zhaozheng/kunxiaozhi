# Implement：统计看板重构（父任务总控）

四个子任务串行执行。父任务只做编排与最终集成审查，不直接写代码。

```
子1 08-28-analytics-metrics-snapshot   口径与日快照层
  ↓
子2 08-28-analytics-api-consolidation  接口整合与清理
  ↓
子3 08-28-analytics-dashboard-ui       前端版面重写
  ↓
子4 08-28-analytics-verification       全量验证与交付
```

## 编排步骤

| # | 动作 | 完成判据 |
|---|------|----------|
| 1 | `task.py start 08-28-analytics-metrics-snapshot`，dispatch `trellis-implement` | 子1 验收标准全过 |
| 2 | dispatch `trellis-check` 复核子1 | lint/mypy/pytest 绿 |
| 3 | 归档子1，start 子2，dispatch `trellis-implement` | 子2 验收标准全过 |
| 4 | dispatch `trellis-check` 复核子2 | 同上 |
| 5 | 归档子2，start 子3，dispatch `trellis-implement` | 子3 验收标准全过 |
| 6 | dispatch `trellis-check` 复核子3 | frontend lint/build/test 绿 |
| 7 | 归档子3，start 子4，dispatch `trellis-implement` | 点击清单交付 |
| 8 | 父任务集成审查：跨子任务验收标准逐条核对 | `prd.md` 的 Acceptance Criteria 全部勾选 |
| 9 | `trellis-update-spec` 沉淀，Phase 3.4 提交 | — |

## 跨子任务集成检查（父任务自己做）

- [x] 六张 KPI 卡、三个环图中心数字、明细表 total、导出 CSV 行数在同一筛选下互相对得上。（自动化：`test_analytics_cross_consistency` 五条等式 + 前端同源契约测试）
- [x] 洞察栏四条数字都能在下方图表/明细里被验算。（`test_analytics_insights_route`；管线纯聚合，无生成内容）
- [x] 同一历史日期两次查询（其间删掉该日一个会话）数字不变。（`test_analytics_snapshot_immutable`；浏览器复验见 `manual-verification.md` §5）
- [x] 五个语言包无缺 key，界面无混合语言。（`usageReportKeys.test.ts` 122+ key × 5 locales）
- [x] 清理清单逐项有"无引用"证据。（子4 `research/removal-evidence.md` E1–E6）

## 全量验证命令

```powershell
uv run ruff check .
uv run mypy src/
uv run pytest tests/api tests/infra -q
cd frontend; pnpm run lint
cd frontend; pnpm run build
```

## 回滚点

| 阶段 | 回滚方式 |
|------|----------|
| 子1 | 两个新集合与 worker 为纯增量；停止 worker 挂载即回到实时聚合 |
| 子2 | 端点参数与清理集中在 `analytics.py` + `storage.py`，`git checkout` 可退 |
| 子3 | 前端新目录 `panels/analytics/`，退回旧 `AnalyticsPanel.tsx` 即可 |
| 子4 | 纯验证与文档，无回滚需求 |
