# Current notification / announcement flow

## Data model

`src/kernel/schemas/notification.py`:

- `NotificationCreate` / `NotificationUpdate` / `Notification`: i18n title/content, `type`, `start_time`, `end_time`, `is_active`. No popup flag.
- Types: `info` | `success` | `warning` | `maintenance`.

Mongo collections (`src/infra/notification/storage.py`):

- `notifications`: announcement documents.
- `notification_dismissals`: unique `(notification_id, user_id)` with `dismissed_at`. Any dismissal hides the item from `/active`.

Indexes created at API startup (`src/api/main.py` → `NotificationStorage.create_indexes`).

## Read path for users

`GET /api/notifications/active` → `get_active_notifications(user.sub, limit=5)`:

- `is_active` and in schedule window
- `$lookup` dismissals; keep only `dismissals: []`
- sort `created_at` desc, clamp limit to 100

Frontend consumers:

- `Header` loads count on mount; bell menu opens `NotificationDialog`.
- `NotificationDialog` fetches `/active` only when `isOpen`; per-item dismiss is permanent.
- `NotificationBanner` fetches `/active` on mount and can dismiss permanently.
- Overlay click and X both call `onClose` with no snooze.

## Write path for admin

`NotificationPanel` / `NotificationFormModal` posts `NotificationCreate` without a popup field. `storage.update` persists schedule/`is_active`/i18n; it does not persist `type` today (out of scope).

## Implication for this task

Permanent dismiss and “don’t auto-popup today” cannot share the current “any dismissal row hides from `/active`” rule. Snooze must keep the row in `/active` (bell/banner) while clearing `should_popup`.
