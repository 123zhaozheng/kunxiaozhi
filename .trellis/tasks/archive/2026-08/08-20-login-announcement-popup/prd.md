# 登录弹出公告与提醒控制

## Goal

管理员可以把重要公告设为登录弹窗；用户看到后能「今日不再提醒」或「不再提醒」。日常公告继续静默，不会打断登录。

## Background

当前公告对用户是静默的：

- 管理端可配置标题、内容、类型、起止时间、是否启用，没有「是否弹出」字段。依据：`src/kernel/schemas/notification.py` 的 `NotificationCreate` / `Notification`；`frontend/src/components/panels/NotificationPanel.tsx` 的 `NotificationFormModal`。
- 用户侧列表弹窗 `NotificationDialog` 只在 Header 菜单点铃铛后打开（`notifDialogOpen` 默认 `false`）。依据：`frontend/src/components/layout/AppContent/Header.tsx`。
- 登录后还有页面横幅 `NotificationBanner` 和原生 App 通知。依据：`frontend/src/components/notification/NotificationBanner.tsx`。
- `POST /{id}/dismiss` 对该用户是永久关闭，之后 `/active` 不再返回。依据：`src/infra/notification/storage.py` 的 `dismiss`、`get_active_notifications`；集合 `notification_dismissals`。
- 前端 dismiss 文案是「关闭」，没有「今日不再提醒」。依据：`frontend/src/i18n/locales/zh.json` 的 `notification.dismiss`。
- `/active` 在 Mongo 侧过滤未关闭公告，默认最多 5 条。依据：`NotificationManager.get_active_notifications`；`tests/infra/test_notification_storage.py`。

## Requirements

- R1. 管理端创建/编辑公告时可选择该公告是否「登录时弹出」，默认否。旧公告无此字段时按否处理。
- R2. 用户进入已登录应用后，若存在生效中、未永久关闭、未处于今日推迟、且标记为弹出的公告，自动打开列表弹窗。停留在当前页不实时打断；新公告在下一次进入应用时再弹。
- R3. 自动弹出的列表只含上述弹出公告。用户从铃铛主动打开时，仍看到全部未永久关闭的生效公告（含静默和今日已推迟的弹出公告）。
- R4. 自动弹出的弹窗提供「今日不再提醒」；点 X 或点遮罩关闭效果相同。当天不再自动弹出**已经看过的那些**弹出公告；铃铛和横幅仍能看到它们。从铃铛主动打开再关闭，不记今日推迟。
- R5. 当天新发布的弹出公告仍应在下一次进入应用时自动弹一次，不受之前「今日不再提醒」影响。
- R6. 弹窗按条提供现有「关闭」（不再提醒）：该用户永久不再看到这条，`/active`、弹窗、横幅都不出现。
- R7. 自动弹出只出现一次列表，不按条数连弹。
- R8. 未标记弹出的公告不自动弹窗，仍可通过铃铛和横幅查看。

## Acceptance Criteria

- [ ] AC1. 管理端表单能打开/关闭「登录时弹出」，保存后列表和再次编辑能看到该状态。对应 R1。
- [ ] AC2. 仅当存在至少一条应弹出公告时，进入已登录应用会自动打开列表；弹窗内只列出这些弹出公告。全部为静默时不自动弹。从铃铛打开的列表仍包含静默公告。对应 R2、R3、R7、R8。
- [ ] AC3. 点击「今日不再提醒」、点 X 或点遮罩关闭自动弹出列表后，当天再次进入应用不会因**已看过的**那些公告再自动弹；铃铛角标/列表和横幅仍能看到它们。从铃铛主动打开再关闭不影响当天是否自动弹。对应 R4。
- [ ] AC4. 用户今日推迟后，管理员当天新发一条弹出公告，用户下一次进入应用仍会自动弹（只含尚未推迟的新公告）。对应 R5。
- [ ] AC5. 对某条公告点击「关闭」后，该用户的 `/active`、弹窗、横幅都不再出现该条。对应 R6。
- [ ] AC6. 旧公告没有弹出标记时按「不弹出」处理，不出现自动弹窗。对应 R1、R8。

## Out of Scope

- 不改原生 App 通知通道（Tauri / Capacitor）的推送策略。
- 不做强制必读（禁止关闭、必须勾选已读才能进应用）。
- 不按角色/用户分组定向投放公告。
- 不在当前停留页面上轮询并实时弹出新公告。
- 不在本次修复 `NotificationUpdate.type` 未写入存储层的既有缺口。
