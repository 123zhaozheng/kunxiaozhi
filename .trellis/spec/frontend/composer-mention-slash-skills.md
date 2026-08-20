# Composer Mention Popup and Slash Skill Emphasis

## 1. Scope / Trigger

Applies when changing `ChatInput` mention (`@`) or slash (`/`) behavior, or the user-message text passed to `onSend`.

- `@` selects a persona/team. Welcome-page card filtering must not hide the mention popup.
- `/` emphasizes already-injected enabled skills. It does not change `enabled_skills` or the skill panel.
- Chip state lives in `ChatView` because Welcome and in-conversation each mount a `ChatInput`.

## 2. Signatures

```ts
// ChatInput.tsx — popup visibility
mention.isActive && mentionMode === "persona"  // MentionPopup
mention.isActive && mentionMode === "team"     // TeamMentionPopup

// chatInputSkillEmphasis.ts
quotedSkillNames(skillNames: string[]): string
buildEmphasizedUserMessage(
  content: string,
  skillNames: string[],
  prefixForNames: (names: string) => string,
): string

// ChatView.tsx
const [emphasizedSkillNames, setEmphasizedSkillNames] = useState<string[]>([])
```

`onSend(message, ...)` still takes one string. Emphasis is a prefix on that string. No new chat API field.

## 3. Contracts

- Render mention popups whenever `mention.isActive`. Do not gate on `!onMentionQueryChange`. Welcome may still pass `onMentionQueryChange` to filter cards.
- Slash query: input from start to cursor starts with `/` and has no space/newline (`getSlashCommandQuery`).
- Slash rows: `/goal` first when it matches, then `skills.filter(s => s.enabled)` via `skillMatchesQuery`.
- Selecting a skill: append name to `emphasizedSkillNames` (no duplicates), strip `/query`, do not send, do not toggle skills.
- Selecting `/goal`: insert `/goal ` as before.
- Submit: `onSend(buildEmphasizedUserMessage(...))`. Chips render inside the composer above the textarea. Send success clears chips; sandbox capacity error restores them.
- Display: `parseEmphasizedUserMessage` returns `{ skillNames, visibleContent }`. User bubble shows a pill (icon, blue names, visible text) and never the must-use sentence. Copy uses `visibleContent`. Fork/retry keep stored full content.
- Unselected enabled skills remain in `enabled_skills` / SkillsStoreBackend.

## 4. Validation & Error Matrix

| Condition | Result |
|---|---|
| Welcome page sets `onMentionQueryChange` | Mention popup still shows |
| Slash query is `null` or dismissed | Menu closed |
| Slash open, zero matching rows, Enter | Consume Enter; do not send `/xyz` |
| Click outside slash menu | Dismiss menu; keep textarea text |
| IME composing / keyCode 229 | Do not treat Enter as select or send |
| Empty `emphasizedSkillNames` | Send original trimmed content |
| Stored message has must-use prefix | Bubble shows pill + visible text; copy is visible text |
| Duplicate skill select | No second chip |

## 5. Good / Base / Bad Cases

- Good: ten enabled skills injected; user `/` picks one; send prefixes `请必须使用「pe」技能。`; the other nine stay injected.
- Base: no chips; send path unchanged; `/goal` still inserts `/goal `.
- Bad: hiding `MentionPopup` behind `!onMentionQueryChange` (welcome `@` looks broken).
- Bad: using `/` to rewrite `enabled_skills` or disable unselected skills.

## 6. Tests Required

- `teamMentionMode.test.ts`: ChatInput source still renders mention popups without `!onMentionQueryChange`.
- `chatInputSlashCommands.test.ts`: `/pe` filters skills; `/goal` stays first; no enabled skills still lists `/goal`.
- `chatInputSkillEmphasis.test.ts`: empty / one / many names; prefix formatting; parse hides prefix and returns visible text.
- User bubble: emphasized messages render pill + visible text, never the must-use sentence; copy uses visible text.
- ChatInput slash keyboard: IME guard; empty list consumes Enter; outside click dismisses.

## 7. Wrong vs Correct

```tsx
// Wrong — welcome @ filters cards and never shows the popup
{mention.isActive && !onMentionQueryChange && mentionMode === "persona" && (
  <MentionPopup ... />
)}

// Correct
{mention.isActive && mentionMode === "persona" && (
  <MentionPopup ... />
)}
```

```ts
// Wrong — / shrinks the injected skill set
onSend(text, { enabled_skills: emphasizedSkillNames })

// Correct — / only prefixes the user message
onSend(buildEmphasizedUserMessage(text, emphasizedSkillNames, prefixForNames))
```
