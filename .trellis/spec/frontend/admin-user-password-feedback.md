# Admin User Password Feedback

## Scenario: Creating an administrator-managed user with a human password

### 1. Scope / Trigger

Use this contract when changing the administrator user form, `UserCreate` submission, password-policy error translation, or `POST /api/users/` validation handling.

The password field is a native controlled input. Typing and paste must follow the same value path. A failed asynchronous save must remain a failure across the parent/child callback boundary so the modal stays open and can show one localized inline error.

### 2. Signatures

```tsx
type UserFormModalProps = {
  onSave: (data: UserCreate | UserUpdate) => Promise<void>;
  onClose: () => void;
};
```

```text
POST /api/users/
Body: UserCreate, including password: string
Success: User
Password-policy failure: HTTP 400 {"detail": "<known policy detail>"}
```

```ts
translateBackendError(message: string): string;
```

Known backend password-policy details map to `backendErrors.passwordPolicy` before they reach the form.

### 3. Contracts

- Keep the password byte-for-byte as entered. Do not trim, normalize, or transform it in the component, API client, route, or storage path.
- Use the native input change path for both typing and paste. Do not add `onPaste` normalization to hide invalid clipboard content.
- Run client-detectable policy checks before submission; backend validation remains authoritative for identifier and strength checks.
- `UserFormModal` calls `onClose()` only after `await onSave(...)` succeeds.
- The parent save handler may show success feedback and refresh the list, but it must rethrow failure. It must not close the modal or swallow the exception.
- The modal catches save failure, preserves all field state, and renders exactly one localized inline error through its existing error region.
- `POST /api/users/` catches domain `ValidationError` and converts it to `HTTPException(400, detail=str(error))`. Unexpected exceptions continue to propagate.
- Raw backend policy strings, exception objects, and duplicate failure toasts are not user-facing output.

### 4. Validation & Error Matrix

| Condition | Expected result |
| --- | --- |
| Compliant typed or pasted value | Exact value submitted; success closes modal and refreshes users |
| Leading/trailing whitespace, newline, or control content | Client policy rejects; no request; localized inline feedback |
| Backend identifier or weak-password rejection | HTTP 400; modal remains open; values preserved; localized inline feedback |
| Unknown API failure | Modal remains open; localized generic operation failure |
| Unexpected backend exception | Propagates to server error handling; never relabeled as validation |

### 5. Good / Base / Bad Cases

- Good: an administrator pastes a compliant password, the exact string is submitted, and the modal closes only after the user is created.
- Base: the clipboard includes a trailing newline; policy feedback appears inline and the administrator can edit the retained value.
- Bad: the parent catches an API error, emits a raw toast, and returns successfully; the child then closes the modal and discards the form.

### 6. Tests Required

- Source contract: assert the native controlled password input retains `value={password}` plus an `onChange` assignment from `event.target.value`, and the request payload uses that state without trimming.
- Submit contract: assert `onClose()` follows the awaited save and the parent failure path rethrows without a duplicate catch toast.
- Policy unit tests: accept a compliant value and reject trailing whitespace, newline, and control content without mutation.
- Error translation tests: every backend password-policy detail maps to `backendErrors.passwordPolicy`; unknown messages keep the established fallback contract.
- Backend route tests: `ValidationError` becomes HTTP 400 with `detail`; unrelated exceptions propagate.
- When a DOM or browser interaction harness is added to the repository, add a rendered `paste -> input/change -> submit` regression that asserts exact typed/pasted payload parity. Until then, static source-contract tests are the supported frontend boundary.

### 7. Wrong vs Correct

```tsx
// Wrong: failure is swallowed, so the modal treats it as success and closes.
try {
  await userApi.create(data);
} catch (error) {
  toast.error((error as Error).message);
}

// Correct: the modal owns failure presentation and closes only on success.
try {
  await userApi.create(data);
} catch (error) {
  if (error instanceof Error) throw error;
  throw new Error(t("users.operationFailed"));
}
```

```python
# Wrong: domain validation can become an internal-server error.
return await manager.register(admin_user_data)

# Correct: convert the expected domain error at the route boundary.
try:
    return await manager.register(admin_user_data)
except ValidationError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
```
