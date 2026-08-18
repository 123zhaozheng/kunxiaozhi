# Implementation Plan

1. Add localized password-requirement and actionable backend-error keys to en/zh/ja/ko/ru.
2. Implement the reusable `PasswordRequirementsHelp` component.
   - Add accessible `CircleHelp` trigger and non-modal requirements dialog.
   - Implement outside/Escape/toggle close and focus return.
   - Implement narrow inline flow and desktop portaled/clamped positioning.
3. Integrate the component beside primary new-password labels in all five flows.
   - Exclude login/current-password and confirmation fields.
   - Preserve forced-page logout, form submission, validation, and exact password handling.
4. Improve existing exact-string backend error translation.
   - Split weak-password, account-identifier, and current-password-reuse details into actionable localized messages.
   - Retain the generic safe password-policy mapping for remaining details and unknown-error behavior.
5. Add focused coverage.
   - All-locale key/content and five-consumer source contracts.
   - Component interaction/accessibility and positioning helper contracts.
   - Password helper boundary cases and `123` strong-context acceptance.
   - Backend error mapping distinctions and generic fallback.
6. Validate.
   - Run focused auth/i18n/backend-error tests, frontend lint, TypeScript no-emit, locale JSON parsing, and `git diff --check`.
   - Start the development server and manually verify the popover at mobile and desktop widths, including no overlap and correct Chinese content.

## Rollback Points

- No backend, database, dependency, or password-policy changes are planned.
- The help component/integrations and the error-message split are separable and can be reverted independently.
