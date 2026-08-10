# Implementation Plan

## 1. Restrict TeamBuilder Persona Candidates

- Filter the existing Team member candidate derivation to exact Search Persona presets.
- Keep the existing search behavior and Team member payload shape.
- Add a focused frontend regression proving Search candidates remain and Fast candidates are excluded.

## 2. Require Team Selection In Chat

- Derive the Team selection-required state from `currentAgent === "team" && !selectedTeamId`.
- Block both click and keyboard submission while that state is true without disabling Team selection controls.
- Automatically open the existing `TeamPickerModal` when the user enters Team mode without a selection.
- Ensure picker dismissal does not immediately reopen during the same Team-mode stay.
- Reuse the modal's existing empty-state New action and `/team` navigation.

## 3. Update Focused Tests

- Replace the existing assertion that Team can submit without a selection.
- Cover Team without selection, Team with selection, Fast/Search unaffected, picker entry behavior, and Search-only Persona filtering.
- Keep existing `team_id` request-scoping tests unchanged unless their assertions require mechanical updates.

## 4. Validation

- Run the affected frontend tests with the repository's `tsx --test` convention.
- Run `pnpm lint` in `frontend/`.
- Run `pnpm build` in `frontend/` for TypeScript and production-build validation.
- Review the final diff for backend/runtime changes; none should be present.

## Risky Points / Rollback

- Picker transition logic must not create a reopen loop.
- Submission must be guarded at the handler/readiness boundary, not only visually.
- All changes are frontend-only and can be rolled back file-by-file without data migration.
