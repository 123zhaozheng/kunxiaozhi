# Analytics 缺陷修复前端审核报告

审核 stage：`review-frontend`  
基线：`main`（075e9041）  
被审 HEAD：`fix/analytics-correctness`（816dcaec）  
范围：本次改动涉及的 `frontend/src/**` analytics 组件、API/类型、五语言 locale 及对应测试。

## 结论

**NEEDS_CHANGES**。核心日期、KPI 同源、筛选参数、分区错误态和当前请求状态保护的实现基本符合设计，但首卡无筛选时的钻取仍然与卡片主值口径冲突；此外新增请求竞态测试没有验证本次最关键的 `finally` loading 回归。

## 发现

### MAJOR-1：无筛选的“活跃用户”卡点击后打开的是消息活跃用户列表

- **位置**：`frontend/src/components/panels/analytics/AnalyticsKpiRow.tsx:264-282`、`frontend/src/components/panels/analytics/AnalyticsPanel.tsx:400`、`frontend/src/components/panels/AnalyticsDrilldownList.tsx:136-146`
- **问题**：首卡在没有 persona/agent 筛选时通过 `kpiUsersValue(summary, false)` 显示 `summary.active_users`，其标签也是“活跃用户”；但卡片无条件传入 `onUsersDrilldown`，主面板点击后只设置 `{ kind: "users" }`。该钻取随后调用 `analyticsApi.listActiveUsers()`，对应 `/users/list` 的消息活跃用户口径，而不是登录活跃用户口径。
- **触发条件/影响**：用户不选任何 persona/agent，点击首卡后，钻取列表的总人数和明细无法与卡片上显示的登录活跃人数相等。用户没有办法从该入口核对卡片主值，造成首卡与钻取之间的统计契约再次失真；有筛选时 `using_users` 与 `/users/list` 才是同源。
- **建议修法**：无筛选时不要把该卡伪装成可钻取，或增加/使用返回登录活跃用户的钻取接口；只有在 persona/agent 筛选态下才将首卡点击绑定到现有 `/users/list`。若产品必须保留一个入口，应将入口文案和列表口径明确改为使用用户，而不能继续显示登录活跃值。
- **辅助核对**：后端 `/users/list` 的语义是活跃用户明细（`src/infra/analytics/storage.py:979-1005` 的文档定义为区间内发过 `user:message`），与本次前端首卡无筛选的登录活跃主值不同。

### MINOR-1：请求竞态测试没有覆盖旧请求 `finally` 关闭新 loading 的回归

- **位置**：`frontend/src/components/panels/analytics/__tests__/analyticsRequestRace.test.ts:16-40`
- **问题**：第 16-34 行只用一个独立的 toy `request()` 验证旧请求不能覆盖 `value`，没有 loading 状态，也没有 `finally`；第 36-40 行只检查每个源码文件出现过一次 `requestId` 比较，不能证明 `then`、`catch`、`finally` 的每个状态写入都受保护。
- **触发条件/影响**：如果以后有人把 `AnalyticsDrilldownList`、`PresetAnalyticsModal` 或主面板某个请求的 `finally` 改回无条件 `setIsLoading(false)`，当前测试仍会通过，但旧请求先结束时会提前关闭新请求的 loading。这正是本次设计声明要求测试捕获的竞态，现有测试不能阻止回归。
- **建议修法**：用两个可控 deferred promise 模拟真实请求，分别记录结果、错误和 loading 状态；先启动旧请求，再启动新请求，让旧请求先 resolve/reject，断言新请求仍保持 loading，最后让新请求结束并断言 loading 才关闭。对每个请求 owner 至少分别覆盖成功、失败和 `finally` 路径，避免只做源码字符串存在性检查。

## 逐项核对结果（未发现实现问题）

- `analyticsDates.ts:46-51`：`todayString()` 先对绝对时间加 8 小时，再使用 `getUTCFullYear/getUTCMonth/getUTCDate`；`addDaysString()` 和日期合法性校验也只用 UTC getter，跨月/跨年/闰日路径正确，审核范围内未残留本地时区 getter。
- 会话 KPI：`analyticsKpi.ts:27-29`、`AnalyticsKpiRow.tsx` 会话卡和 `AnalyticsTopRow.tsx` 两个会话环图均通过 `kpiSessionsValue()` 使用 `active_sessions`；副行使用 `new_sessions`，会话趋势也使用 `active_sessions`。
- 首张用户卡主值：`AnalyticsKpiRow.tsx:264-277` 通过动态 `isFiltered` 选择 `active_users`/`using_users`，没有硬编码第二参数；无筛选副行显示 `using_users`，筛选态使用用户口径成立。
- 首张用户卡 sparkline：`AnalyticsKpiRow.tsx:205-206、251-252` 仅在筛选态使用 `/users/active` 返回的 `activeTrend`；无筛选时为空，不会把登录主值与消息趋势画在同一张卡的 sparkline 中。
- 主面板请求竞态：`AnalyticsPanel.tsx:207-222、285` 以请求序号保护批次结果；usage 明细请求的 `then/catch/finally` 在 `AnalyticsPanel.tsx:303-316` 均检查取消标志。`AnalyticsDrilldownList.tsx:120-171` 和 `PresetAnalyticsModal.tsx:138-155` 的成功、失败、finally 状态写入均检查请求序号，未发现当前实现遗漏的旧请求 loading 关闭路径。
- 主面板局部失败：`AnalyticsPanel.tsx:211-283` 使用 `Promise.allSettled`，并按 summary、insights、trend、三个环图和反馈区分别建立错误状态；`ChartCard`、KPI、趋势和反馈区均能显示对应错误，不会因单个环图 reject 直接打挂整页。
- 前端 usage API 筛选序列化：`services/api/analytics.ts` 通过 `buildUsageQuery` 将 persona/agent（及 role）传给各 usage 端点；`firstUse` 和模型钻取参数也在对应 list API 中序列化，未发现前端新增参数被静默丢弃。
- 类型兼容性：本次 `src/types/analytics.ts` 没有把新的后端统计字段改成必需字段；新增的 `firstUse` 为可选。现有 `active_sessions` 等字段在基线类型中已存在，因此没有发现本补丁新增必填字段导致旧响应立即无法编译/消费的问题。
- i18n：新增 `analytics.insights.peakDrillHint`、`analytics.overview.newSessions` 在 `zh/en/ja/ko/ru.json` 五个文件均存在，analytics 结构和 weekdays 数组长度一致，未发现新增 key 缺失或错位。
- 新增交互：峰值洞察和新增用户洞察使用真实 `<button type="button">`，环图/趋势/反馈图表新增了可读的 `role="img"`/`aria-label`；未发现本次新增按钮缺少键盘语义。
- 已执行的定向验证：`frontend` 下运行 `node --test --import tsx src/components/panels/analytics/__tests__/*.ts`，29 项通过。该结果不能抵消 MINOR-1 所述的测试覆盖缺口。
