# PR1: 后端 analytics 基础设施 + 全局看板

## Goal

实现 `/api/analytics` 后端模块和 `/analytics` 前端全局看板页面（不含角色智能体 Modal 和钻取明细，那是 PR2 范围）。

> 详细需求参考父任务 PRD：`.trellis/tasks/06-11-analytics-dashboard/prd.md`

## Scope

### 后端
- 新建 `src/infra/analytics/__init__.py`、`storage.py`、`manager.py`
- 新建 `src/api/routes/analytics.py`
- 在 `src/kernel/schemas/analytics.py` 定义 Pydantic schemas
- 在 `src/api/main.py` 注册新路由
- 为相关集合（`traces`、`sessions`、`users`、`feedback`）建立统计所需索引
- 实现 **7 个聚合查询端点**（不含 `/presets/{preset_id}` 单条）：
  1. `GET /api/analytics/overview?start=...&end=...`
  2. `GET /api/analytics/users/active?start=...&end=...`
  3. `GET /api/analytics/users/heatmap?start=...&end=...`
  4. `GET /api/analytics/sessions/trend?start=...&end=...`
  5. `GET /api/analytics/tokens/by-model?start=...&end=...`
  6. `GET /api/analytics/tokens/by-preset?start=...&end=...`
  7. `GET /api/analytics/tokens/trend?start=...&end=...`

> 反馈相关端点（feedback/summary、feedback/by-preset）和单角色智能体端点延后到 PR2。

### 前端
- 添加 Recharts 依赖到 `frontend/package.json`
- 新建 `frontend/src/services/api/analytics.ts`
- 新增 `TabType analytics` 到 `frontend/src/components/layout/AppContent/types.ts`
- 在 `frontend/src/components/layout/AppContent/TabContent.tsx` 添加新条目
- 在 `frontend/src/App.tsx` 添加 `/analytics` 路由 + SEO 页面
- 新建 `frontend/src/components/panels/AnalyticsPanel.tsx`
- 在 `frontend/src/components/layout/UserMenu.tsx` System 分组加入"统计"入口（BarChart3 图标）
- 新建 i18n 键 `analytics.*`（en/zh/ja/ko/ru 五语言）

## Pages Build Up

- 概览卡片（4 个）：活跃用户数 / 总会话数 / 总 token / 点赞率
- 用户板块：活跃用户折线图 + 时段热力图
- 会话板块：会话/消息趋势折线图
- Token 板块：按模型饼图 + 按角色智能体 Top 10 饼图 + Token 趋势折线图
- 时间筛选器（1天/7天/30天/自定义日期区间）：所有图表共享

## Acceptance Criteria

- [ ] 后端 analytics 模块完整实现，7 个端点返回正确数据
- [ ] MongoDB 索引建立完成
- [ ] 路由已注册，权限校验生效（settings:manage）
- [ ] `/analytics` 页面可访问（带左侧侧边栏）
- [ ] 4 个概览卡片显示在顶部
- [ ] 用户/会话/Token 三大板块的图表正确渲染
- [ ] 时间筛选器切换可刷新所有图表
- [ ] UserMenu 中"统计"入口在 settings:manage 权限下可见
- [ ] i18n 五语言完整
- [ ] Lint / typecheck / 测试全部通过

## Out of Scope (for PR1)

- 角色智能体卡片分析按钮（PR2）
- 角色智能体分析 Modal（PR2）
- 钻取明细（PR2）
- 单角色智能体端点 `/api/analytics/presets/{preset_id}`（PR2）
- 反馈相关端点 `/api/analytics/feedback/*`（PR2）
- 全局看板中的"反馈板块"将在 PR2 补齐
