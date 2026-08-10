# Design: Team Search Persona And Chat Selection Gate

## Scope And Boundaries

This is a frontend-only change. It reuses the existing Persona response field, Team selection state, picker modal, and `/team` creation route. Backend request contracts and Team runtime behavior remain unchanged.

## Team Builder Flow

`TeamBuilder` already loads Persona presets and derives a searched candidate list. Add the Search-only predicate at that derivation boundary:

```text
personaPresetApi.list
  -> presets
  -> preferred_agent_id === "search"
  -> text search
  -> selectable Team member candidates
```

Use an exact Search match so missing or Fast values fail closed in the UI. Keep saved Team member payloads unchanged because members continue to reference `persona_preset_id` only.

## Chat Selection Flow

The existing `currentAgent` and `selectedTeamId` values are sufficient:

```text
switch to Team
  -> selectedTeamId exists: keep current selection and allow submit
  -> selectedTeamId absent: open TeamPickerModal and block submit
       -> team exists: select it -> allow submit
       -> no teams: existing empty state + New action -> /team
```

Open the picker from the existing chat selector component when entering Team with no selection. Avoid adding global state or a second team-list request solely for gating.

Derive a local Team readiness condition in the chat input submission boundary. Both the button state and submit handler must honor it so keyboard submission cannot bypass the visual disabled state. The toolbar/picker controls remain interactive while message submission is blocked.

## State And Compatibility

- Preserve `selectedTeamId` when switching away from Team; returning to Team may reuse the previous explicit selection.
- Keep the existing request scoping: `team_id` is sent only when `currentAgent === "team"` and a selection exists.
- Do not change backend behavior for direct Team requests without `team_id`.
- Do not store a Team-member mode snapshot or add save/runtime validation.

## Testing Strategy

- Update the stale Team mention/send source regression to assert selection-gated submission.
- Add focused coverage for automatic picker opening on Team entry without selection and no reopening when a selection exists.
- Cover Search-only Persona candidate filtering.
- Retain existing request-scoping, Team route-state, welcome gallery, and selector placement coverage.

## Risks And Rollback

- Risk: an effect that opens the picker on every render could reopen after the user dismisses it. Trigger only on the relevant mode/selection transition or otherwise preserve deliberate dismissal until the next Team entry.
- Risk: guarding only the button leaves Enter-key submission available. Keep the guard in the shared submit path.
- Rollback is limited to the touched frontend components/tests; no persisted data or API contract changes are involved.
