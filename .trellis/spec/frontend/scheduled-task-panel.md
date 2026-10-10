# Scheduled Task Panel

> Code: `frontend/src/components/panels/ScheduledTaskPanel/*` (user side),
> `frontend/src/components/panels/ScheduledTaskAdminPanel/*` (admin side),
> `frontend/src/components/chat/ChatMessage/items/ScheduledTaskDraftCard.tsx`,
> routes `/scheduled-tasks` (tab `scheduled`) and `/admin/scheduled-tasks` (tab `scheduledAdmin`).

- Audience is non-technical bank staff: never show cron, UTC or seconds. Frequency is picked with large buttons; times with quick chips plus a native time input.
- The preview (`POST /api/scheduled-tasks/preview`) is the only source of the human description and next-run list; the Save button is disabled until the preview is valid. Backend validation messages are shown verbatim.
- `cleanSpec()` must be used before sending a spec (drops irrelevant fields, normalises `run_at`); the exact request body is contract-tested in `__tests__/taskEditorPayload.test.ts`.
- Display times in `Asia/Shanghai` (`formatFireTime`).
- Feature gate: the sidebar entry and the route require `Permission.SCHEDULED_TASK_USE`; when `SettingsContext.enableScheduledTasks` is false the panel shows a "功能已关闭" banner instead of the task list.
- Editor persona row uses `PersonaPresetSelector` (hidden without `persona_preset:read`); saving submits `persona_preset_id` (null = cleared). Task cards show the persona avatar/name via `PersonaAvatarBadge.tsx` and a「专家已不可用」badge when `persona_available` is false.
- Admin-paused tasks (`paused_by === "admin"`): the card shows `pause_reason` + `adminPausedHint`; resume and run-now buttons are hidden/disabled for the owner — the backend rejects them with 403 anyway, the UI just must not offer them.
- WebSocket `scheduled_task:run_finished` is handled in `useWebSocketNotifications`; it dispatches `scheduled-task:refresh` so an open panel reloads. Event constants live in `utils/scheduledTaskEvents.ts` (`SCHEDULED_TASK_REFRESH_EVENT`) — do not inline the string.
- Chat draft cards (`ScheduledTaskDraftCard`, rendered by `MessagePartRenderer`) show all four draft states (待确认 / 已确认 / 已取消 / 已过期); confirm/cancel call the draft API and, on success, `dispatchScheduledTaskRefresh()` so an open task panel reloads. Tasks created this way carry the「聊天创建」badge (`created_via === "chat"`).
- Destructive actions use `ConfirmDialog`; all strings live under `scheduledTask.*` / `scheduledTasksAdmin.*` (zh/en; others fall back to en).

## Admin panel (`ScheduledTaskAdminPanel`)

- Route `/admin/scheduled-tasks` is wrapped in `ProtectedRoute` with `Permission.SCHEDULED_TASK_ADMIN`; the `UserMenu` entry is gated by the same permission. API client: `services/api/scheduledTaskAdmin.ts`.
- Cross-user list supports search by task name / creator username, filters by status and last result, pagination via `skip`/`limit`; response items extend the task with `owner_id` / `owner_name`.
- Pausing opens `PauseReasonDialog` — the reason is required (1–200 chars; backend enforces the same range, errors surface verbatim).
- Run history reuses `TaskRunsDrawer` with an injected `fetchRuns` (admin endpoint, no owner scoping) and `allowOpenSession={false}`: an administrator cannot open another user's chat session, so the jump link is hidden rather than 404-ing.
