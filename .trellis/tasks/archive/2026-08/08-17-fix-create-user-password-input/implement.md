# Implementation Plan

1. Add or extend focused frontend tests around the create-user form.
   - Cover a real paste/input sequence with an exact compliant password payload.
   - Assert typed and pasted values are equivalent.
   - Cover pasted trailing whitespace/newline/control content as an intentional local rejection.
   - Cover API password-policy failure retaining the open modal and showing one localized inline error.
2. Correct frontend submit control flow.
   - Ensure the modal closes only after a successful save.
   - Propagate or explicitly return save failure from the parent handler.
   - Preserve entered values and route failure text to the existing inline error region.
   - Remove duplicate/raw password-policy toast behavior while retaining localized fallback for unrelated failures.
3. Extend password-policy backend-error localization using the established shared translation/i18n pattern.
   - Cover length, whitespace/control, character-class, identifier, byte-length, and weak-password variants exposed by the backend.
   - Preserve the existing unknown-error fallback contract.
4. Add backend route regression coverage and map domain validation correctly.
   - Catch `ValidationError` explicitly in admin create-user routing.
   - Return HTTP 400 with standard FastAPI `detail`.
   - Confirm unexpected exceptions are not swallowed.
5. Run focused validation, then package-level checks.
   - Frontend: relevant Vitest tests plus the repository's type-check/lint commands.
   - Backend: focused pytest route/policy tests plus the repository's standard test/lint/type checks available for the touched modules.
   - Review successful create/edit behavior and confirm no password mutation was introduced.

## Rollback Points

- Frontend submit/error-flow changes and backend route mapping are independent commits or coherent diff sections and can be reverted separately if a regression is found.
- No schema, persisted-data, dependency, or configuration changes are planned.
