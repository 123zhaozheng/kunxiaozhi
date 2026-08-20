# Announcement Login Popup

## Scenario: Optional login popup vs silent announcements

### 1. Scope / Trigger

Use this contract when changing system announcements (`notifications` / `notification_dismissals`), `GET /api/notifications/active`, `POST /{id}/dismiss`, admin create/update, or the Header/`NotificationDialog` login popup. Do not add a second announcement system or treat any dismissal row as “hide from `/active`”.

### 2. Signatures

```python
# src/kernel/schemas/notification.py
class NotificationCreate(..., popup: bool = False)
class NotificationUpdate(..., popup: Optional[bool] = None)
class NotificationDismiss(snooze_until: Optional[datetime] = None)
class Notification(..., popup: bool = False, should_popup: bool = False)

# Storage / API
GET  /api/notifications/active?popup_eligible=false  -> list[Notification]  # bell/banner, default limit 5
GET  /api/notifications/active?popup_eligible=true   -> list[Notification]  # auto-open, clamp 100
POST /api/notifications/{id}/dismiss                 # empty body = forever; JSON snooze_until = today
```

```typescript
notificationApi.getActive()
notificationApi.getPopupEligible()  // /active?popup_eligible=true
notificationApi.dismiss(id, snoozeUntil?: string)
shouldAutoOpenNotifications(items): boolean  // any should_popup
localEndOfDayUtcIso(now?: Date): string
```

Mongo `notification_dismissals`: unique `(notification_id, user_id)` with `dismissed_at`, `forever: bool`, `snooze_until: datetime | null`.

### 3. Contracts

| Field / flag | Meaning |
|---|---|
| `popup` | Admin “登录时弹出”. Missing Mongo field → `false`. |
| `should_popup` | `popup` and not forever-dismissed and (`snooze_until` missing or `now >= snooze_until`). Only meaningful on `/active`. |
| Empty dismiss body | `forever=true`, `snooze_until=null`. Current banner/dialog 「关闭」. |
| `{ "snooze_until": "<iso>" }` | `forever=false`. Item stays in `/active`; `should_popup` false until that instant. |
| Legacy dismissal (`dismissed_at` only) | Treat as `forever=true`. |
| Auto dialog close (今日不再提醒 / X / backdrop) | Snooze **listed** popup ids to local end-of-day UTC. |
| Bell `mode=manual` close | Must not snooze. |
| Auto list | Only `should_popup` items. |
| Bell/banner list | All non-forever-dismissed active items (including snoozed popup). |

Do **not** use the bell `limit=5` to decide auto-open. A newer silent announcement must not hide an older popup item. `popup_eligible=true` is clamped to `NOTIFICATION_LIST_LIMIT_MAX` (100), not 5.

No in-page polling. A new popup announcement appears on the next authenticated enter.

### 4. Validation & Error Matrix

| Condition | Behavior |
|---|---|
| Dismiss body empty / whitespace | Forever close |
| Dismiss JSON invalid | HTTP 422 |
| Dismiss JSON `{ "snooze_until": null }` or omitted field after parse | Forever close |
| Unknown notification id on dismiss | Best-effort upsert (existing behavior) |
| `/active` without login | 401 (existing auth dep) |
| Missing `popup` on old docs | `false`; no auto-open |

### 5. Good/Base/Bad Cases

- Good: admin sets `popup=true`; user enters app; auto dialog lists that item; X snoozes until local end of day; bell still shows it; new `popup=true` later today auto-opens on next enter.
- Base: all announcements `popup=false` (default / legacy). Bell and banner only; no auto dialog.
- Bad: `$match` `dismissals: []` so a snooze row hides the item from `/active` (breaks “今日不再提醒 still in bell”). Bad: auto-open from `getActive()` limit 5.

### 6. Tests Required

- `tests/infra/test_notification_storage.py`: forever vs snooze; legacy row = forever; missing `popup` → false; popup-eligible not limited to 5; valid snooze excluded from popup-eligible; expired snooze restores `should_popup`.
- `frontend/src/components/notification/__tests__/loginPopup.test.ts`: auto-open only when `should_popup`; missing `should_popup` does not auto-open; snooze ids are the `should_popup` ids.

### 7. Wrong vs Correct

#### Wrong

```python
{"$match": {"dismissals": {"$eq": []}}}  # any dismissal hides from bell
items = get_active_notifications(user_id, limit=5)
auto_open = any(n.popup for n in items)
```

#### Correct

```python
# forever (or legacy) excluded from /active; snooze rows kept
# should_popup = popup and not forever and snooze expired/absent
eligible = get_popup_eligible_notifications(user_id)  # clamp 100, not 5
auto_open = any(n.should_popup for n in eligible)
```

```typescript
// auto dialog close
await Promise.all(ids.map((id) => notificationApi.dismiss(id, localEndOfDayUtcIso())));
// per-item 关闭 / banner X
await notificationApi.dismiss(id); // no body
```
