# feat: LambChat 全局统计与角色智能体指标系统

## Goal

为 LambChat 添加全面的统计分析能力，包括全局数据看板和**角色智能体（PersonaPreset，角色广场中的 AI 预设）**维度的指标展示，让管理员和配置者能够洞察系统使用情况、token 消耗、用户活跃度等关键指标。

> **术语澄清**：本文档中"角色"一律指 **角色广场中的角色智能体（PersonaPreset）**，不是权限管控的 Role（RBAC 角色）。

## What I Already Know

### 后端数据源
- 30 个 MongoDB 集合，核心可用集合：traces(运行事件+token), sessions, feedback, users, persona_presets, file_records
- Token 跟踪已存在：traces 集合中每个 run 事件含 input_tokens, output_tokens, total_tokens, duration, cache_creation_tokens, cache_read_tokens, model_id
- 现有仅 5 个统计端点：feedback stats(2), revealed file stats(1), health/ready(2)
- User schema 有 updated_at 字段（登录/活动时自动更新），可直接作为活跃判定依据

### 前端现状
- 无图表库（需新增 Recharts）
- PersonaPresetCard 操作：Pin/Favorite/Use/Edit/Delete/Copy，无分析按钮
- FeedbackPanel 有 StatsCards 设计模式可参考
- 14 个 Tab 面板，无 Analytics Tab
- UserMenu 已有 3 分组（Personal/Admin/System），可加 Analytics 入口
- 权限系统完善，40+ Permission 枚举值

## Requirements

### 权限与可见性
- 统计面向两类用户：**管理员**（看全局看板 + 所有角色智能体指标）+ **角色智能体配置者**（看自己创建的 global preset 的指标）
- 后端 API 统一复用 `settings:manage` 权限（不新增独立权限）

### 全局看板（`/analytics` 页面）
- 入口：右上角头像下拉菜单 System 分组中新增"统计"菜单项（BarChart3 图标），需 `settings:manage` 权限
- 页面布局：和 /chat 一样带左侧侧边栏，响应式卡片网格（Grafana 风格）
- **概览卡片（4 个）**：活跃用户数 / 总会话数 / 总 token 消耗 / 点赞率
- **用户统计**：活跃用户折线图 + 时段热力图（横轴星期 × 纵轴小时，颜色深浅=请求量）
- **会话/消息统计**：总会话数、总消息数、日均会话数、趋势折线图
- **Token 消耗**：总 token、按模型分（饼图）、按角色智能体分 Top 10（饼图）、趋势折线图（仅展示 token 数）
- **反馈统计**：点赞/踩总数、点赞率、按角色智能体分反馈

### 角色智能体分析 Modal
- 来源：角色智能体卡片上新增分析按钮（图标建议 BarChart3）
- 可见条件：仅 global preset + `channel:manage` 权限（与企业微信配置按钮同条件）
- Modal 含完整时间筛选器（与全局看板一致）
- **基础指标**：总消息数、总会话数、活跃用户数、token 消耗总计
- **反馈指标**：点赞率、点踩原因分布（横向柱状图，按原因码对比，4 个原因：与问题无关/内容不完整/内容错误/数据分析错误）

### 图表钻取
- 点击图表元素（饼图某模型/某角色等）→ 跳转到明细列表视图
- 明细列表权限统一复用 `settings:manage`
- 元素 → 明细列表的映射实施时按图表分别约定

### 时间筛选器
- 所有趋势图共享一个时间筛选器
- 快捷时段：1天 / 7天 / 30天
- 高级：自定义日期区间选择器

### 后端架构
- **新增模块**：`src/infra/analytics/`（storage + manager）+ `src/api/routes/analytics.py`（挂载 `/api/analytics`）
- **性能策略**：实时 MongoDB aggregation + 索引优化，不做预聚合或 Redis 缓存
- **API 端点**（详情拆分，方便按需加载）：
  - `GET /api/analytics/overview?start=...&end=...` — 概览卡片
  - `GET /api/analytics/users/active?start=...&end=...` — 活跃用户趋势
  - `GET /api/analytics/users/heatmap?start=...&end=...` — 时段热力图
  - `GET /api/analytics/sessions/trend?start=...&end=...` — 会话/消息趋势
  - `GET /api/analytics/tokens/by-model?start=...&end=...` — 按模型分 token 饼图
  - `GET /api/analytics/tokens/by-preset?start=...&end=...` — 按角色智能体分 token Top 10
  - `GET /api/analytics/tokens/trend?start=...&end=...` — token 趋势
  - `GET /api/analytics/feedback/summary?start=...&end=...` — 反馈汇总
  - `GET /api/analytics/feedback/by-preset?start=...&end=...` — 按角色智能体分反馈
  - `GET /api/analytics/presets/{preset_id}?start=...&end=...` — 单角色智能体完整指标

### 前端架构
- **新增依赖**：Recharts（📊）
- **新增组件**：`AnalyticsPanel`（全局看板）、`AnalyticsCharts/*`（各图表组件）、`PresetAnalyticsModal`（角色分析 Modal）
- **新增 API 服务**：`frontend/src/services/api/analytics.ts`
- **新增路由/TabType**：`analytics`，跳路径 `/analytics`
- **新增 i18n 键**：`analytics.*` 命名空间，en/zh/ja/ko/ru 五语言
- **修改 UserMenu**：System 分组中加入"统计"菜单项

## Acceptance Criteria

- [ ] 后端 analytics 模块（storage + manager）建立，支持以上 10 个端点
- [ ] MongoDB 相关集合新增必要索引支撑聚合查询
- [ ] `/analytics` 页面可访问（带左侧侧边栏），展示 4 个概览卡片
- [ ] 用户板块：活跃用户折线图 + 时段热力图
- [ ] 会话板块：趋势折线图
- [ ] Token 板块：总数字 + 饼图（按模型 + 按角色智能体 Top 10）+ 趋势折线图
- [ ] 反馈板块：汇总统计 + 按角色智能体列表
- [ ] 时间筛选器（1天/7天/30天/自定义）作用于所有图表
- [ ] 头像菜单中"统计"入口在 settings:manage 权限下可见，点击跳转 /analytics
- [ ] 角色智能体卡片分析按钮在 global preset + channel:manage 权限下显示
- [ ] 点击分析按钮弹 Modal，展示角色指标的图表
- [ ] 所有图表元素可点击钻取到明细列表
- [ ] i18n 键在五语言文件中齐备
- [ ] Lint/typecheck/test 全部通过

## Definition of Done

- 后端 analytics 模块完整实现，10 个 API 端点通过测试
- 前端 /analytics 页面完整可交互
- PersonaPresetCard 上的分析按钮 + Modal 完整实现
- 所有图表（饼图、折线、热力图、柱状图）正常渲染和交互
- 钻取明细列表可访问
- i18n 五语言文件齐备
- Lint/typecheck/test 全部通过
- 不引入 unrelated 变更

## Decisions (ADR-lite)

### D1: 路由位置选择头部菜单而非 Settings
- **Context**: 用户希望统计入口放在右上角头像菜单中
- **Decision**: 头像菜单 System 分组加入口，跳转到独立 `/analytics` 页面
- **Consequences**: 统计数据独立，不与配置混淆

### D2: 后端权限复用 `settings:manage`
- **Context**: 是否新增 `analytics:read` 权限
- **Decision**: 复用已有 `settings:manage`，不新增
- **Consequences**: 简单一致；但未来如开放统计给非管理员需新增权限

### D3: 实时 aggregation 无缓存/预聚合
- **Context**: MongoDB 聚合性能考虑
- **Decision**: 直接跑 aggregation，靠索引优化，不做预聚合
- **Consequences**: 实现简单；数据量大时可能慢，需要索引兜底

### D4: 数据仅展示 token 数值
- **Context**: 是否换算费用
- **Decision**: 不换算费用，仅展示原 token 数
- **Consequences**: 不需要价格表配置，简洁

### D5: 图表库选 Recharts
- **Context**: 项目无图表库
- **Decision**: 选 Recharts（React 生态主流）
- **Consequences**: 包体轻量、声明式 API、易集成

### D6: 趋势统一用折线图
- **Context**: 趋势展示形式
- **Decision**: 折线图统一
- **Consequences**: 视觉一致

### D7: 热力图为时段热力图
- **Context**: GitHub 式日历热力图 vs 时段矩阵热力图
- **Decision**: 时段热力图（星期×小时）
- **Consequences**: 更适合企业内部使用高峰分析

### D8: 钻取明细列表复用 settings:manage
- **Context**: 明细列表权限粒度
- **Decision**: 统一 settings:manage
- **Consequences**: 与看板同权限，简单一致

## Implementation Plan (staged)

### PR1 — 数据基础设施 + 全局看板基础
**后端**：
- 新建 `src/infra/analytics/__init__.py`、`storage.py`、`manager.py`
- 新建 `src/api/routes/analytics.py` 实现 7 个聚合查询端点（不含预设单条 `/presets/{id}`）
- 为相关集合（`traces`、`sessions`、`users`、`feedback`）建立统计所需索引
- 新建 schemas：`src/kernel/schemas/analytics.py`（OverviewResponse, TrendDataPoint 等）

**前端**：
- 添加 Recharts 依赖到 package.json
- 新建 `frontend/src/services/api/analytics.ts`
- 新增 TabType `analytics` + 路由
- 新建 `AnalyticsPanel`（含 4 个概览卡片 + 用户折线 + 时段热力 + 会话趋势 + Token 总览 + 反馈汇总）
- 修改 UserMenu.tsx 加入"统计"入口
- 新建 i18n 键 `analytics.*`

### PR2 — 角色智能体 Modal + 钻取明细
**后端**：
- 实现 `GET /api/analytics/presets/{preset_id}` 端点（含基础指标 + 反馈指标）
- 实现反馈按角色智能体聚合端点 `GET /api/analytics/feedback/by-preset`（含点踩原因码统计）
- 实现 3-4 个钻取明细端点（如 `/sessions/list`、`/runs/list`、`/feedback/list` 配合筛选条件）

**前端**：
- 新建 `PresetAnalyticsModal`（复用概览卡片 + 反馈指标）
- 在 PersonaPresetCard.tsx 加入分析按钮（条件渲染）
- 实现饼图、柱状图组件，绑定 onClick 跳转到对应明细列表
- 新建明细列表组件（如 SessionsListView、FeedbackListView）

## Out of Scope

- Token 费用换算（仅展示 token 数值）
- AI 模型价格配置
- 登录历史独立位（用 updated_at 满足）
- MCP 工具调用统计
- 技能执行统计
- 用户消息内容查看（仅看消息数和元数据，不看内容）
- 实时推送（WebSocket/SSE）更新统计
- CSV/Excel 导出
- 通知/告警阈值
- LLM 在统计功能（如 AI 解读趋势）

## Technical Notes

- 数据源：`traces` 集合的 `events` 数组（token:usage 事件类型）含完整 token 数据
- `persona_presets` 有 `usage_count` 字段但未在 handler 中递增更新
- MongoDB aggregation pipeline 的 $facet 可一次查询出多维度统计
- 用户活跃判定：直接查 users 表的 `updated_at >= start`，去重 user_id
- 热力图时段数据：通过 aggregation `$dateToString` + `$hour`/`$dayOfWeek` 分组
- i18n 提取有脚本 `pnpm i18n:extract`，新键以 `analytics.*` 命名空间组织
