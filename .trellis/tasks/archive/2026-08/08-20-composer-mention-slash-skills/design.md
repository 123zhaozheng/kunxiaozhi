# Design: composer @ mention fix and / skill emphasis

## Approach

Keep this frontend-only and small.

1. `@` always opens `MentionPopup` / `TeamMentionPopup` when mention is active. Welcome-page card filtering can stay, but it must not hide the popup.
2. `/` reuses the existing slash drop-up. Rows are `/goal` plus currently enabled skills from the `skills` prop. Typing `/pe` filters skills with `skillMatchesQuery`.
3. Selecting a skill adds a chip, strips the `/query` token, and does not send. Selecting `/goal` keeps today's insert-`/goal ` behavior.
4. Chips live in `ChatView` state so Welcome and in-conversation share them while composing. They render inside the composer card, above the textarea. Send success clears them.
5. On submit, prefix the outgoing user message with a short must-use instruction. Do not touch `enabled_skills`.
6. User bubbles parse that prefix and render a pill (icon + blue skill names + user text). The must-use sentence is never shown. Copy uses visible text only.

## Boundaries

| In | Out |
|---|---|
| `ChatInput`, slash helpers, mention popup visibility | Backend chat/agent APIs |
| `ChatView` chip state on `chatInputProps` | Skill panel / persona skill persistence |
| i18n for slash/chip/emphasis copy | New MCP or skill protocol |

## Data flow

```
enabled skills (already injected)
  → ChatInput slash list (filter by /query)
  → ChatView emphasizedSkillNames[]
  → chips inside composer, above textarea
  → onSend: `${emphasis}\n\n${userText}`  (model + storage)
  → clear chips after accepted send
```

`@` flow stays: detect mention → search presets/teams → `onUsePersonaPreset` / `onSelectTeam`. Only the welcome-page `!onMentionQueryChange` popup guard changes.

## Contracts

- Slash query: still “starts with `/`, no space yet”, same as `getSlashCommandQuery`.
- Skill rows: `skills.filter(s => s.enabled)`, match name/description/tags.
- Emphasis text: one line, e.g. `请必须使用「A」「B」技能。` i18n-backed. Prefix only; do not rewrite the user sentence.
- Chips: names + close. Duplicate select is a no-op.
- Keyboard: slash menu first, then mention, then send. Ignore Enter while `isComposing`.

## Compatibility

- `/goal` remains a slash command. Skill select must not steal `/goal` when the query matches the goal command.
- Disabled / not-injected skills never appear.
- No backend migration. Old sessions without chips behave as today.

## Trade-offs

- Storage still holds the prefix so reload/fork keep model instructions. Display hides it with a parser on `「name」` + blank-line split. No new backend field.
- Lifting chip state to `ChatView` is extra wiring, but local `ChatInput` state would drop chips after the first send.

## Rollback

Revert the ChatInput / ChatView / helper / i18n / test files. No data migration.
