# 统计看板重建 — 手工验证清单（浏览器）

> 自动化覆盖见子任务 `08-28-analytics-verification/research/verification-log.md`；
> 本清单覆盖只能人眼确认的交互、渲染与真实数据行为。逐项勾选，全部 ✅ 即通过。

## 0. 前置条件

- [ ] 后端 `uv run uvicorn src.api.main:app`（或现有部署），MongoDB / Redis 可用
- [ ] 前端 `pnpm dev`，以 **拥有 `settings:manage` 权限的账号**登录
- [ ] 打开 设置 → 统计看板（AnalyticsPanel），默认区间为最近 30 天

## 1. 一屏看板与 KPI 六卡

- [ ] 首屏一屏内完整呈现：KPI 六卡 / 三个环图 / 趋势折线 / 洞察栏 / 明细表，无需滚动找核心数字
- [ ] 六卡数字与 `/usage/summary` 响应逐一对得上（active_users、using_users、new_sessions、active_sessions、user_messages、total_tokens）
- [ ] 「新建会话」卡数字 == `new_sessions`（非活跃会话数，口径已定）
- [ ] 每卡环比（较上一区间）显示 ↑/↓/持平，颜色方向正确；上一区间无数据时显示「—」而非 0%
- [ ] 无权限账号（无 `settings:manage`）访问看板：接口 403，页面给出提示而非白屏

## 2. 等式核对（同一筛选下五个出口互相对得上）

不勾选任何筛选，记录区间后逐项核对：

- [ ] summary.active_sessions == `/usage/by-persona` 各行 active_sessions 之和
- [ ] summary.user_messages == `/usage/by-user` 全量行 user_messages 之和
- [ ] summary.total_tokens == `/tokens/by-model` 各项之和
- [ ] `/usage/by-user` 的 total == summary.using_users（每用户单 persona 时）
- [ ] 导出 CSV 行数 == `/usage/by-user` 的 total（未超 10,000 行上限时）
- [ ] 三个环图中心数字与对应 KPI 卡一致（persona 环图中心 = 总会话、模型环图中心 = 总 token、agent 环图中心 = 总会话）

再任选一个 Persona 筛选，重复上面前 3 条（筛选后数字应同步变小且等式仍成立）：

- [ ] 切换筛选后所有请求的 URL 参数完全一致（DevTools Network 对比 `buildUsageQuery` 序列化结果）
- [ ] 清除筛选后数字恢复

## 3. 筛选与日期

- [ ] 日期区间改动：所有卡片/图表/明细/洞察同一次刷新（无半新半旧）
- [ ] 非法输入（end < start、非日期）：后端 400，前端有可读提示
- [ ] agent 筛选、角色筛选各试一次：六卡、环图、明细同步收敛
- [ ] 快捷区间按钮（如 7/30/90 天）切换正确

## 4. 时区边界（UTC+8）

- [ ] 选「今天」单天区间：凌晨 00:00 后产生的会话/消息应计入今天
- [ ] 构造或找一条 23:59（本地时间）的记录，确认它落在当天而非次日（跨 0 点不误判）
- [ ] 区间为单天时趋势折线只有一个点且日期字符串为所选日期
- [ ] **浏览器时区改写**：把操作系统/浏览器时区改为 `UTC` 或 `America/New_York`（Chrome：`--lang` 或 DevTools Sensors → Timezone），重开页面选同一日期区间：六卡数字与区间边界应与步骤 2 记录完全一致（日界按服务端 UTC+8 展开，不随客户端时区漂移）

## 5. 快照不可变与历史数据

- [ ] 选择历史区间（如上个月），记录六卡数字 A
- [ ] **删除该区间内某一天的一个会话**（聊天页删除或后台），刷新同一区间：数字仍为 A —— 快照已冻结，硬删不会缩掉历史数字
- [ ] 在历史区间对应日期的快照集合（`analytics_daily_snapshots`）中，重复请求不产生重复文档（`$setOnInsert` 幂等）
- [ ] 「今天」区间仍走实时聚合：新产生一条消息后刷新，user_messages +1
- [ ] 选一个早于快照存在的远古区间：降级实时聚合，页面正常出数（不报错）

## 6. 空状态与边界

- [ ] 选一个确定无数据的区间：六卡显示 0/—，环图显示空态文案，明细表空态，不报错不白屏
- [ ] 明细表翻页：下一页/上一页正常；超限页返回空列表而非错误
- [ ] 大数据量明细（>20 行）：分页器总数与 total 字段一致

## 7. 导出 CSV（Excel 兼容）

- [ ] 明细表导出按钮生成 `analytics-usage.csv`，浏览器触发下载
- [ ] 用 Excel 直接打开：中文列头/姓名无乱码（文件带 BOM，utf-8-sig）
- [ ] CSV 行数 == 明细表 total（未超上限）；带筛选导出时行数 == 筛选后 total
- [ ] 列内容与明细表可见列一一对应，日期为 ISO 格式

## 8. 洞察栏

- [ ] 四条洞察（峰值时段 / Top3 token 用户 / 增长最快 Persona / 新增用户）在无数据时隐藏或显示占位，不显示 NaN
- [ ] 峰值时段文案与 `/usage/insights` 的 peak 字段一致（Asia/Shanghai 口径）

## 9. 视图适配

- [ ] 桌面 1920×1080：版面不重叠、环图不变形
- [ ] 平板 ~768px：卡片换行正确
- [ ] 手机 ~390px：可纵向滚动，无横向滚动条，表格有横滚或卡片化

## 10. 回归（看板之外）

- [ ] Persona 详情弹窗（PresetAnalyticsModal）仍正常打开、出数
- [ ] 设置页其他模块（角色 / 用户 / Persona 管理）加载无回归
- [ ] 控制台无新增报错/告警（Network 无 500）

## 记录

| 日期 | 执行人 | 结果 | 备注 |
| --- | --- | --- | --- |
| | | | |
