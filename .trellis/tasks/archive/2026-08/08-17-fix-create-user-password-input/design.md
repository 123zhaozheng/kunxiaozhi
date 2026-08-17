# Technical Design

## Scope and Boundaries

The fix spans three existing boundaries without changing password policy:

1. The create-user modal owns local validation, pending state, form preservation, and inline feedback.
2. The Users panel owns API invocation and successful list refresh, but must not convert a rejected save into apparent success.
3. The admin user API route converts domain `ValidationError` into FastAPI's standard HTTP 400 response.

Shared frontend backend-error translation may be extended for known password-policy messages so backend-only validation remains localized and safe.

## Input and Submit Flow

- Continue using the native controlled password input and its normal change event for both typing and paste.
- Keep the exact string in component state and in the `UserCreate` payload. No trimming or Unicode normalization is allowed.
- Run the existing client password-policy validation before calling the API. Invalid clipboard whitespace/control content follows this same path.
- Await the save operation. Close the modal only when the operation succeeds.
- On failure, retain field state, end the pending state, and render a localized message through the modal's existing inline error UI.

The parent save function must communicate failure unambiguously, preferably by allowing the API exception to propagate after any required panel-level cleanup. A success boolean is acceptable only if it cannot accidentally treat failure as success and remains consistent for create and edit paths.

## Error Contract

- `POST /api/users/` catches domain `ValidationError` explicitly and raises `HTTPException(status_code=400, detail=str(error))`, matching the project's route-boundary convention and existing registration behavior.
- Unexpected exceptions continue to propagate; the route must not broadly catch and relabel server failures.
- Known password-policy details are translated at the shared frontend error boundary or mapped to stable localized form messages before display.
- The modal displays one inline error. It must not emit the same failure as a raw toast.
- Unknown errors use the existing localized generic user-operation failure message, not raw exception serialization.

## Compatibility

- Successful create and edit flows continue to close the modal and refresh users.
- Passwords remain byte-for-byte unchanged from input to request.
- Backend validation remains authoritative, including account-identifier and zxcvbn checks not fully duplicated client-side.
- Shared error mappings must preserve existing consumers and unknown-error fallback behavior.

## Test Strategy

- Frontend component regression: ordinary paste updates controlled state and submits the exact compliant value; typing the same value yields the same payload.
- Frontend validation regression: pasted newline/whitespace/control content blocks submission and shows localized inline feedback.
- Frontend API failure regression: backend-only policy rejection keeps the modal open, preserves values, and shows one inline localized error without a raw toast.
- Backend route regression: a mocked or isolated `ValidationError` from user registration becomes HTTP 400 with standard `detail`; unexpected errors are not swallowed.
- Existing password-policy and backend-error translation tests remain green.

## Risk and Rollback

The main regression risk is changing create/edit error behavior together because they share the modal and save handler. Tests must cover success and failure for the relevant mode. The change is localized and can be rolled back by reverting the modal/save control-flow and route mapping changes; no data migration or feature flag is required.
