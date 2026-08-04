# WeCom Persona Feedback Notification

> Executable contracts for notifying persona "notification targets" when a persona receives a WeCom thumbs-up/down, plus the binding mechanism and known platform limitations.

## Scenario: persona feedback → Web + WeCom notification

### 1. Scope / Trigger

- Trigger: WeCom `feedback_event` (thumbs up/down) that successfully writes a `Feedback` record via `_handle_wecom_feedback` (handler.py). Notification fires only for `type=1/2` (like/dislike), never for `type=3` (cancel).
- Web-side feedback (`/api/feedback` → `FeedbackManager.submit_feedback`) does **not** trigger notification — the trigger point lives only inside the WeCom feedback handler.

### 2. Signatures

```python
async def notify_persona_feedback(
    *,
    preset_id: str,
    preset_name: str,
    rating: str,          # "up" | "down"
    operator: str,        # WeCom userid (= username = 工号)
    comment: str | None,
    aibotid: str,
    session_id: str,
    run_id: str,
) -> None: ...

class WeComNotifyBindingStorage:
    async def upsert(self, aibotid: str, username: str) -> bool: ...
    async def is_bound(self, aibotid: str, username: str) -> bool: ...
    async def list_bound(self, aibotid: str, usernames: list[str]) -> set[str]: ...
```

Frontend contract:

```typescript
interface FeedbackNotification {
  type: "notification:feedback";
  data: {
    preset_id: string;
    preset_name: string;
    rating: "up" | "down";
    operator: string;
    comment: string | null;
    user_question: string | null;
    model_output: string | null;
    ts: string;
  };
}
```

### 3. Contracts

| Contract | Required behavior |
|----------|-------------------|
| Identity mapping | `username` = 工号 = WeCom `userid`. `UserStorage.get_by_username(username).id` is the web user_id for WebSocket routing |
| Targets config | `persona_wecom_config.feedback_notify_targets: list[str]` (usernames). Missing/empty → no notification at all |
| Binding collection | `wecom_notify_bindings`, unique index `(aibotid, username)`, `bound_at` timestamp |
| Bind command | Exact match `content.strip() == "绑定通知"`, intercepted **before** persona routing; replies a confirmation and returns (never enters the AI session) |
| Trigger flow | `aibotid → get_preset_id_for_aibotid → get_persona_wecom_config → targets`; empty → early return |
| Web channel | For each target username → `send_to_user_with_broadcast(user.id, message)`; unresolved user → log + skip |
| WeCom channel | Only targets with a binding record; text via `WeComBotManager.send_message(aibotid, username, text)`; no binding → skip silently |
| Run context | `_load_run_context(session_id, run_id)` reads trace events: first `user:message` content = question; `message:chunk` contents joined by `seq` = model output; each truncated to 200 chars |
| Failure isolation | Per-target/per-channel try/except with logger; notification failure never blocks feedback write |
| Admin API | `GET/PUT /persona-presets/{preset_id}/wecom/notify-targets`, permission `channel:manage`, `_validate_global_preset`, 404 when no wecom config |

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| No targets / config missing | No notification (silent early return) |
| Target username not in users collection | Web channel skips with warning; WeCom channel unaffected |
| Target not bound | WeCom channel skipped; Web channel still delivers |
| Trace lookup fails | `user_question`/`model_output` = None; notification still sent |
| `rating="down"` comment already contains "原因:" prefix | Notification uses `反馈：{comment}` (no duplicated 原因 prefix) |
| aibotid has no preset mapping | Skip notification with warning log |

### 5. Known limitations (learned 2026-08-04)

1. **Segmented replies lose feedback linkage (open issue).** Only the first stream frame carries `feedback={"id": run_id}` (`reply_stream`). Later segments, non-stream fallback, and timeout fallback are sent via `aibot_send_msg` (`send_proactive_message`/`send_message`), which carries **no feedback field**. WeCom only emits `feedback_event` for messages that had `feedback` set, so liking a later segment produces no callback and no sync. Long answers (> `segment_target_chars`) are therefore unreliable for feedback. Fix direction: verify whether `aibot_send_msg` accepts a `feedback` field at protocol level; if yes, attach `feedback={"id": run_id}` to every segment.
2. **Web channel requires the recipient online.** WebSocket `send_to_user_with_broadcast` delivers only to live connections; offline recipients lose the event (no persisted in-app inbox in MVP).
3. **One aibotid maps to one preset.** `WeComBotManager._aibotid_to_preset` keeps the last-loaded config; duplicate aibotid across presets is fragile (mapping can flip with document order / reload). Orphan `persona_wecom_config` rows (persona deleted) should be cleaned up.
4. **Frontend display is three-layer.** `appNotificationService` supports only native runtimes (tauri / capacitor-android); in a plain browser it returns `"unsupported"` and silently drops notifications. Browser delivery must combine `appNotificationService` + `useBrowserNotification` + `toast` (same pattern as `onTaskComplete`).

### 6. Tests Required

| Test | Assertion point |
|------|-----------------|
| `tests/infra/notification/test_feedback_notifier.py` | No targets early return; web pushes resolved users only; wecom only bound users; run context (question/output) in payload and text; exception isolation |
| `tests/infra/agent/wecom/test_binding.py` | upsert idempotent, is_bound, list_bound, unique index |
| `tests/infra/agent/test_wecom_binding_command.py` | Bind command intercepted before persona routing; confirmation reply; normal messages unaffected |
| `tests/api/test_persona_wecom_notify_targets.py` | 403 without `channel:manage`; GET targets + bound status; PUT full replace; 404 without config |
| Frontend `useWebSocket.test.ts` | `notification:feedback` parsed and dispatched; unknown events ignored |
