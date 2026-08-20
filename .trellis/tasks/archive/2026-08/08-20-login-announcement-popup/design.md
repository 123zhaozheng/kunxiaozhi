# Login announcement popup — Technical Design

## Boundaries

- Extend the existing notification schema, storage, `/active` + dismiss API, admin form, Header auto-open, and `NotificationDialog`.
- Do not add a second announcement system, polling, or changes to Tauri/Capacitor notify adapters.
- Do not change `NotificationUpdate.type` persistence.

## Data model

Add to `NotificationCreate` / `NotificationUpdate` / `Notification`:

- `popup: bool = False` — admin “登录时弹出”. Missing Mongo field validates as false.

Add to the **user-facing** `Notification` response (populated only on `GET /active`):

- `should_popup: bool = False` — `popup` is true, not forever-dismissed, and snooze is absent or expired.

`notification_dismissals` document:

| Field | Forever close | Today snooze |
|---|---|---|
| `notification_id`, `user_id` | required | required |
| `dismissed_at` | set | set |
| `forever` | `true` | `false` |
| `snooze_until` | `null` | UTC datetime (end of user’s local day) |

Unique index stays `(notification_id, user_id)`. Snooze then forever-close updates the same row.

**Legacy rows** (today’s documents: `dismissed_at` only): treat as `forever=true`.

## Data flow

```text
Admin form popup toggle
  -> NotificationCreate/Update.popup
  -> notifications.popup

GET /active
  -> active + in-window + not forever-dismissed
  -> each item: popup, should_popup (now < snooze_until ⇒ false)

Header mount
  -> getActive()
  -> if any should_popup: open NotificationDialog(mode=auto)
  -> dialog lists only should_popup items

Auto close (今日不再提醒 / X / backdrop)
  -> POST dismiss each listed id with snooze_until
  -> those ids stay in /active, should_popup=false

Bell open (mode=manual)
  -> full /active list; close does not snooze

Per-item 关闭
  -> POST dismiss with no snooze (forever)
  -> gone from /active, banner, dialog
```

New popup announcements after snooze have no dismissal row, so `should_popup=true` on the next app enter.

## Contracts

### `GET /api/notifications/active`

Keep auth. Change the aggregation so a snooze-only dismissal does **not** drop the document. Attach `should_popup`. Bell count continues to include snoozed popup items.

Do not reuse `limit=5` for deciding auto-open: if the newest five are all silent, a sixth popup item would never trigger. Auto-open must consider all currently eligible popup items (still clamp with `NOTIFICATION_LIST_LIMIT_MAX`). Practical approach: `get_active_notifications` still returns the bell list (limit 5), and a sibling query or the same pipeline without the small cap collects `should_popup` ids. Prefer one storage method that:

1. loads forever-excluded active notifications with the existing limit for the list payload
2. separately (same collection filters) checks whether any `popup=true` row is eligible to auto-open, not capped at 5

Frontend auto-opens if that check is true, and the auto dialog lists those popup-eligible rows (bounded by 100).

### `POST /api/notifications/{id}/dismiss`

Backward compatible: empty/omitted body = forever (current).

Optional JSON body:

```json
{ "snooze_until": "2026-08-20T15:59:59.999Z" }
```

- with `snooze_until`: upsert dismissal `forever=false`, store the timestamp
- without: upsert `forever=true`, `snooze_until=null`

Frontend computes local calendar end-of-day and sends ISO UTC. Server only compares `utc_now()` to `snooze_until`.

Invalid id: keep current dismiss behavior (best-effort upsert; do not 404 unless already required).

### Admin create/update

Persist `popup` on create and update (`model_fields_set` like `is_active`).

## Frontend

- Types: add `popup`, `should_popup`.
- `NotificationFormModal`: toggle next to 启用, default off, submit `popup`.
- `Header`: after `getActive`, if any `should_popup`, `setNotifDialogOpen(true)` with auto mode. Fetch popup-eligible list for the auto dialog (see contract above). Guard with a ref so React StrictMode/remount does not double-open before snooze returns.
- `NotificationDialog`:
  - `mode: "auto" | "manual"`
  - auto: filter/list `should_popup`; footer button「今日不再提醒」; X/backdrop → same snooze then `onClose`
  - manual: current full list; X/backdrop only `onClose`
  - per-item「关闭」unchanged (forever)
- i18n: `notification.popupOnLogin`, `notification.dontRemindToday` in zh/en/ja/ko/ru.
- Extract a tiny pure helper for “should auto-open” / “ids to snooze” so it can be unit-tested without the dialog.

`NotificationBanner` stays on `/active` and forever-dismiss only.

## Compatibility / rollout

- Old notifications: missing `popup` → false; no auto-open.
- Old dismissals: missing `forever` → forever; still hidden from `/active`.
- No backfill job. Indexes unchanged except we may add a sparse/no-op field; unique pair index remains.
- Rollback: stop sending `popup: true` and ignore `should_popup` on the client; snooze rows become inert if `/active` reverts to “any dismissal hides”.

## Trade-offs

- Snooze is **per announcement**, stored **server-side**, so it follows the account across devices (same as forever dismiss). Local-only snooze would re-popup on phone after closing on desktop.
- No live popup while the tab stays open; matches login/cold-start, not a notification bus.
