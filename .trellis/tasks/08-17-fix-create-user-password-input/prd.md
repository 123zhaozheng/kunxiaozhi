# Fix Create-User Password Input and Validation Feedback

## Goal

Make administrator user creation reliable for both typed and pasted passwords, and ensure password-policy failures keep the form open with clear, localized inline feedback.

## Background

Repository research found no paste-specific handler or transformation. The create-user password is held by a native controlled input and is sent unchanged through the API to backend validation. A normal compliant pasted value should therefore behave like a typed value. Clipboard whitespace, trailing newlines, or invisible control characters are the likely explanation for paste-only policy rejection and need explicit regression coverage.

The confirmed defect is in failure control flow. The parent save handler catches the API error, shows raw backend text in a toast, and does not propagate failure. The modal then closes as though creation succeeded, bypassing its existing inline error area. The admin create-user route also does not map password-policy `ValidationError` to HTTP 400, and backend-only strength or account-identifier messages are not translated by the frontend.

## Requirements

- Preserve password input exactly. Do not trim, normalize, or mutate typed or pasted password content.
- Treat an ordinary compliant paste and typing the same value as equivalent input paths.
- Reject prohibited whitespace, newlines, control characters, and other policy violations explicitly rather than silently modifying clipboard content.
- Keep the create-user modal open and preserve its values when client-side or backend password validation fails.
- Display password-policy failures in the modal's existing inline error area using localized, actionable text; do not also show a duplicate raw-error toast.
- Convert backend password-policy `ValidationError` at the admin route boundary to the standard HTTP 400 `detail` response.
- Keep the current password policy and backend authority unchanged.
- Add focused frontend and backend regression tests for the confirmed and suspected failure paths.

## Acceptance Criteria

- [ ] Pasting a policy-compliant password can successfully create a user.
- [ ] Typing the same policy-compliant password continues to work and produces an equivalent request payload.
- [ ] Pasted content is not silently dropped, trimmed, normalized, or validated against stale form state.
- [ ] A pasted password containing trailing whitespace, a newline, or prohibited control content is rejected with localized policy feedback.
- [ ] Client-detectable password-policy failures are not submitted.
- [ ] A backend-only strength or account-identifier rejection returns HTTP 400, keeps the modal open, preserves form values, and displays localized inline feedback.
- [ ] No raw backend password-policy string, stack trace, exception object, or duplicate toast is presented to the administrator.
- [ ] Existing successful create/edit behavior and password-policy rules remain unchanged.
- [ ] Focused frontend and backend tests pass.

## Out of Scope

- Changing password complexity rules.
- Adding a custom paste handler unless a browser-level regression test demonstrates that the native input path fails.
- Redesigning unrelated administrator or user-management screens.
- Changing login, password reset, or forced-password-change behavior except for reuse of shared error translation that preserves compatibility.

## Technical Notes

- Evidence and file anchors are recorded in `research/create-user-password-investigation.md`.
- This is a cross-layer task covering frontend submit/error handling, shared backend-error translation, the admin user route, and focused tests.
