# Scheduled Task Panel

> Code: `frontend/src/components/panels/ScheduledTaskPanel/*`, route `/scheduled-tasks`, tab `scheduled`.

- Audience is non-technical bank staff: never show cron, UTC or seconds. Frequency is picked with large buttons; times with quick chips plus a native time input.
- The preview (`POST /api/scheduled-tasks/preview`) is the only source of the human description and next-run list; the Save button is disabled until the preview is valid. Backend validation messages are shown verbatim.
- `cleanSpec()` must be used before sending a spec (drops irrelevant fields, normalises `run_at`).
- Display times in `Asia/Shanghai` (`formatFireTime`).
- WebSocket `scheduled_task:run_finished` is handled in `useWebSocketNotifications`; it dispatches `scheduled-task:refresh` so an open panel reloads.
- Destructive actions use `ConfirmDialog`; all strings live under `scheduledTask.*` (zh/en; others fall back to en).
