# Implement: composer @ mention fix and / skill emphasis

## Checklist

1. Extract slash matching so `/goal` and enabled skills share one list. Filter skills with `skillMatchesQuery`. Keep `/goal` insert behavior. Add helper to strip the current `/query` after a skill is chosen.
2. Add a tiny emphasis helper: given selected skill names, return the must-use prefix string. Unit-test empty / one / many names.
3. `ChatInput`: always render mention popups when mention is active (remove the `!onMentionQueryChange` guard). Extend slash drop-up to show skill rows. Selecting a skill adds/removes chips via props, strips `/query`, does not send. Guard IME on Enter.
4. Render persistent chips above the textarea (reuse `ToolbarChip` or the same chip look). Close button removes one name.
5. `handleSubmit`: if chips exist, prefix emphasis text onto the content passed to `onSend`. Do not clear chips after send. Do not change skill toggles.
6. Lift `emphasizedSkillNames` to `ChatView` and pass through `chatInputProps` so Welcome and in-conversation inputs share the same chips.
7. i18n for slash empty state, chip labels, and the must-use prefix (zh/en plus existing locale files the chat placeholder already uses).
8. Tests: slash filter + `/goal` coexistence; mention popup no longer gated by welcome projection; emphasis prefix builder.

## Validation

```bash
cd frontend
npx tsx --test src/components/chat/__tests__/chatInputSlashCommands.test.ts
npx tsx --test src/components/chat/__tests__/teamMentionMode.test.ts
npx tsx --test src/components/chat/__tests__/chatInputSkillEmphasis.test.ts
```

If the emphasis test path differs, run the new test file next to the helper. Also typecheck/lint the touched frontend files with the repo's usual frontend commands.

## Risky files

- `frontend/src/components/chat/ChatInput.tsx` — mention, slash, submit, chips in one component.
- `frontend/src/components/layout/AppContent/ChatView.tsx` — two ChatInput mounts; chip state must be shared.
- `frontend/src/components/chat/chatInputSlashCommands.ts` — keep `/goal` tests green.

## Rollback

Git revert the task files. No backend or data cleanup.

## Done when

PRD AC1–AC8 pass from the tests above plus a quick manual check: `@` popup on welcome, `/pe` filter, multi-select chips persist after send, `/goal` still works.
