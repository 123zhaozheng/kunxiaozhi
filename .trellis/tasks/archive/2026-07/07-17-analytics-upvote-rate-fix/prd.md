# 点赞率 bugfix + overview 卡片下钻

## Goal

修复 overview 点赞率恒为 0：统计查询与 feedback `rating` 枚举对齐（`up`/`down`）；点赞率卡片可点击进入反馈列表。

## Requirements

- `AnalyticsStorage.get_overview`（及同类）禁止 `rating: "like"`，使用 `up`/`down`。
- `up_vote_rate = up / (up+down)`，无反馈时为 0。
- 前端点赞率 StatsCard 可点击 → 反馈下钻/列表（时间范围继承看板）。
- 补充/调整单测覆盖：有 up/down 时 rate > 0。

## Acceptance Criteria

- [x] 存在 up/down 反馈时 overview.up_vote_rate 符合公式。
  - evidence: `uv run pytest tests/infra/test_analytics_storage_upvote_rate.py -q` → 4 passed
  - `test_get_overview_up_vote_rate_uses_up_down_not_like`: 3 up / 1 down → 75.0
- [x] 全仓无 analytics 路径再查 `like` 作为 rating。
  - evidence: pipeline `$match.rating = {$in: ["up","down"]}`；源码仅注释提及 like
- [x] UI 可从点赞率进入反馈列表。
  - evidence: `AnalyticsPanel` StatsCard `onClick={() => setDrilldown({ kind: "feedback" })}`，drilldown 复用面板 `range`

## Dependencies

- 无；可并行开展。

## Notes

- 父任务曾假定：`get_overview` 使用 `rating: "like"`，模型为 `Literal["up","down"]`。
- 当前仓库代码已是 up/down；本任务加固公式共享、显式 `$in: ["up","down"]`、前端可点下钻、单测防回归。
- 父任务：`07-17-persona-template-and-analytics`
