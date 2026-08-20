# Implementation Plan

1. Schema: add `popup` to create/update/response; add `should_popup` on response. Frontend types match.
2. Storage: write `popup` on create/update. Change `get_active_notifications` so forever dismissals are excluded, snooze-only are kept. Compute `should_popup`. Add a query for popup-eligible rows not capped at 5 (max 100). Extend `dismiss` to accept optional `snooze_until`; legacy rows count as forever.
3. API: optional dismiss body `{ snooze_until }`; `/active` returns the new fields. Manager passes the body through.
4. Admin form: 「登录时弹出」toggle, default false; list/edit show it.
5. Header + dialog: auto-open when popup-eligible items exist; auto mode lists only those items; X/backdrop/今日不再提醒 snooze those ids; bell path unchanged.
6. i18n zh/en/ja/ko/ru.
7. Tests: storage pipeline/forever vs snooze; missing `popup` → false; helper for auto-open/snooze ids. Focused pytest + frontend test for the helper.

## Ordered checklist

- [ ] Pydantic + TS types
- [ ] Storage create/update/dismiss/active + popup-eligible query
- [ ] API dismiss body + `/active` fields
- [ ] Admin form toggle
- [ ] Header auto-open + dialog mode
- [ ] i18n
- [ ] Tests + ruff/vitest on touched files

## Validation Commands

```bash
uv run pytest tests/infra/test_notification_storage.py
uv run ruff check src/kernel/schemas/notification.py src/infra/notification src/api/routes/notification.py
```

Frontend helper test command: the existing vitest file next to the new helper, e.g. under `frontend/src/components/notification/__tests__/`.

## Risky files / rollback

| Area | Risk |
|---|---|
| `get_active_notifications` pipeline | Treating snooze as forever would hide banner/bell items; treating forever as snooze would bring dismissed items back |
| `limit=5` on `/active` | Popup item older than five newer silent items would never auto-open unless popup-eligible query is uncapped |
| Auto-open on Header mount | StrictMode double fetch / close=snooze race |
| Dismiss body | Must keep no-body = forever for current banner/dialog 关闭 |

Rollback: revert schema/storage/API/frontend files. Existing forever dismissals remain valid. Snooze-only rows would be ignored by old code that hides any dismissal (bell would drop those items until the snooze row is removed or expires and is rewritten).

## Follow-up before `task.py start`

- Planning summary given to the user; user explicitly approved that summary.
- `implement.jsonl` / `check.jsonl` have real spec/research entries.
- Do not `task.py start` in the same turn as finishing these artifacts.
