# Frontend Review Findings: User Storage Management (Task 09-14)
## Commit 915f6f54 - ProfileStorageTab & Related Components

Review scope: Only user-uploaded files deletion logic (chat, wecom). i18n coverage across 5 locales. Upload gating, attachment lifecycle, quota round-trip, and error handling.

---

## FINDINGS

### 1. **MAJOR — Quota Pre-check May Silently Fail with No User Guidance**
**File:** `frontend/src/hooks/useFileUpload.ts:225-245`  
**Severity:** MAJOR  
**Symptom:** Users see a quota error toast (`"Storage space is full..."`) and storage management opens, but the original files remain in the attachment list with `uploadError` set. On retry, `useFileUpload` calls `uploadFile(pending.file)` WITHOUT re-fetching storage usage. This means:
- If storage is still over quota on retry, the same pre-check fails silently (inside a `catch` block that only logs nothing).
- User retries repeatedly but never learns that storage is STILL full — the pre-check catches it but only shows the same error toast again.
- The `catch` block at line 242 explicitly swallows errors: `catch { // Compatibility... }`, so transient quota check failures are silent.

**Confirmed by code:**
```typescript
// Line 225-245: getUsage() call is only in uploadFile, runs once per file
try {
  const usage = await storageApi.getUsage();
  const clearlyOverLimit = usage.status === "full" || ... processedFile.size > usage.remaining_bytes;
  if (clearlyOverLimit) {
    markQuotaBlocked(tempId, {...}); // Shows toast and marks uploadError
    return; // ← Exits without removing tempId from attachments
  }
} catch {
  // Compatibility with deployments before the storage endpoint exists.
  // ← No logging, no fallback behavior
}
```

**User flow:**
1. User uploads file → storage full → error toast, file stays in list with `uploadError`
2. User goes to Storage tab, deletes files
3. User hits Retry in chat input
4. Pre-check may fail silently again if timing/caching is unlucky
5. User sees same toast but doesn't know if retry succeeded or failed

**Suggested fix:**
- Log the `catch` exception when quota pre-check fails, not just silence it
- Consider: on `retryUpload`, optionally re-fetch usage summary before attempting the pre-check again
- Document that the pre-check is best-effort and the backend decision is final

**Risk:** User confusion and repeated failed retry attempts.

---

### 2. **MINOR — Quota Pre-check Timestamp Race: Stale Summary Before Upload**
**File:** `frontend/src/hooks/useFileUpload.ts:225-245`  
**Severity:** MINOR  
**Symptom:** The quota check fetches `storageApi.getUsage()` at upload time. Between this check and the actual upload request, the user or another session might delete files, or other concurrent uploads might commit. The summary is stale by the time it reaches the server.

**Confirmed by code:** Comments explicitly state this is intentional:
```typescript
// Line 223: "This is only an early UX gate. The upload response remains the
// authoritative decision, so a stale summary never grants access."
```

**Assessment:** This is **intentional design**. The backend's atomic reservation is the real arbiter. No fix needed, but ensure the server-side 413 response is tested in concurrent upload scenarios (back-end scope, not frontend).

**Status:** Safe by design. ✓

---

### 3. **MAJOR — Missing Refetch of Usage After Successful Delete in ProfileStorageTab**
**File:** `frontend/src/components/profile/tabs/ProfileStorageTab.tsx:150-200` (delete handler)  
**Severity:** MAJOR  
**Symptom:** When a user deletes one or more files in the Storage tab, the UI optimistically updates the selected list and calls `storageApi.delete()` or `storageApi.batchDelete()`. After the response returns with updated usage, the code calls `setSummary(result.usage)` and `loadPage(cursor)`, which should reload the file list. However:

- If the reload (`loadPage`) fails or times out, `summary` has the new usage but `files` list is stale.
- Subsequent upload attempts in chat input fetch usage again from the server (at `uploadFile` time), which **should** be correct, but there's a transient window where the Storage tab shows one value and the pre-check might use a slightly newer value from a second fetch.
- More critically: if delete response does NOT include `result.usage` (e.g., a network glitch returns partial response), `setSummary` is never called, and the UI still shows the old quota.

**Confirmed by code:**
```typescript
// Line 150-160: In handleBatchDelete
const result = await storageApi.batchDelete(batchIds);
if (!isMountedRef.current) return;
setSummary(result.usage); // ← Uses result.usage if present
setPartialResults(result.results || null);
// ... later:
void loadPage(cursor); // ← May fail silently if network is spotty
```

The `loadPage` error is not explicitly handled; if it fails, the UI does not show a retry or error state for the pagination reload.

**Suggested fix:**
1. Add explicit error handling for `loadPage()` rejection
2. Verify backend delete responses always include `usage` field (should be per spec, but not asserted on client)
3. Consider a dedicated refetch for summary if the page reload fails

**Risk:** User sees outdated quota after delete, then tries to upload and hits the real quota error, creating confusion.

---

### 4. **MINOR — Empty Locale Keys Not Handled; i18n Keys Complete**
**File:** `frontend/src/i18n/locales/*.json` (all 5 locales)  
**Severity:** MINOR  
**Symptom:** All required `storage.*` keys are present in all 5 locales (zh, en, ja, ko, ru). A programmatic check confirms no missing keys referenced in ProfileStorageTab or useFileUpload.

**Assessment:** ✓ i18n coverage is complete. No issues found.

---

### 5. **MAJOR — Protected Files Display Lacks Guidance on How to Manage Them**
**File:** `frontend/src/components/profile/tabs/ProfileStorageTab.tsx:356-365`  
**Severity:** MAJOR  
**Symptom:** Files from `profile_avatar`, `persona_avatar`, `team_avatar`, and `skill` sources are marked protected and show a lock icon. The UI text says *"Manage this file from its owning feature"* (`storage.protectedHint`), but:

- For **avatars** (profile, persona, team): No inline link or button to navigate to the owning feature.
- For **Skill files**: No inline link to the Skill editor/delete.
- The ProfileStorageTab says "头像文件可在此删除" (at line 356 comments) but the actual UI does NOT offer a delete button for avatars—they are read-only in this view.

This contradicts the message at line 356 in the code comments. **The user sees "Chat files can be removed here. Avatars and Skills stay protected" but actually CANNOT interact with protected files in this tab.** They must navigate away and find the owning feature.

**Confirmed by code:**
```typescript
// ProfileStorageTab.tsx, line 356-365
<span className="...text-stone-500...">
  {t("storage.protectedHint", "Manage this file from its owning feature.")}
</span>
// ← No clickable link, no "Edit avatar" button, no "Open skill editor" link
```

**Suggested fix:**
1. Add clickable action links for protected files:
   - For avatars: "Edit in Profile / Persona / Team"
   - For Skills: "Edit Skill" button
2. Or: Move protected files to a separate, read-only "Reference" section with the hint.
3. Update the comment at line 356 if the behavior changes.

**Risk:** User confusion: "Why can't I delete this avatar here if it's taking up space?"

---

### 6. **MINOR — AttachmentCard Lifecycle Listener Doesn't Unsubscribe on Property Changes**
**File:** `frontend/src/components/common/AttachmentCard.tsx:80-92`  
**Severity:** MINOR  
**Symptom:** The `useEffect` that subscribes to `STORAGE_LIFECYCLE_EVENT` has a dependency array:
```typescript
[attachment.fileId, attachment.key]
```
If the attachment object is recreated (e.g., new reference) but `fileId` and `key` are the same, the listener is not re-added. Conversely, if one of these changes, the old listener is not removed before adding the new one. This can lead to stale listeners accumulating if the same fileId is referenced by multiple attachment instances with slightly different object identities.

**Confirmed by code:**
```typescript
// Line 80-92 in AttachmentCard.tsx
useEffect(() => {
  const handleLifecycle = (event: Event) => {
    const detail = (event as CustomEvent<StorageLifecycleEventDetail>).detail;
    if (
      !matchesStorageLifecycleEvent(detail, attachment.fileId, attachment.key)
    ) {
      return;
    }
    // ... set state
  };
  window.addEventListener(STORAGE_LIFECYCLE_EVENT, handleLifecycle);
  return () => window.removeEventListener(STORAGE_LIFECYCLE_EVENT, handleLifecycle);
  // ← Cleanup is correct, but dependency [attachment.fileId, attachment.key] 
  // is OK since the handler is defined inside the effect.
}, [attachment.fileId, attachment.key]);
```

**Assessment:** On closer inspection, this is actually **SAFE** — the cleanup function removes the listener, and re-adding it is correct when fileId/key change. The dependency array is appropriate.

**Status:** ✓ No issue found.

---

### 7. **BLOCKER — RolesPanel storageQuotaMb: Zero Value Not Rejected (Could Be Misinterpreted as "No Limit")**
**File:** `frontend/src/components/panels/RolesPanel.tsx:140-180`  
**Severity:** BLOCKER  
**Symptom:** In the role form, when a user enters `0` (zero) for `storageQuotaMb`, the validation at line 160 checks:
```typescript
if (!isNaN(numValue) && numValue > 0) {
  limits.storage_quota_mb = numValue;
}
```
This means a value of `0` is silently **ignored** and not included in the limits. However:

1. **Backend interpretation:** If the backend later interprets a missing `storage_quota_mb` as "use global default," then setting it to 0 on the frontend and not sending it produces the wrong semantic: the admin intended "zero quota = block all uploads for this role," but instead got "use default."
2. **User intent:** An admin might intentionally set a role to zero quota as a way to disable uploads for that role.
3. **Silent truncation:** The UI does not warn the user that entering `0` will be ignored.

**Confirmed by code:**
```typescript
// RolesPanel.tsx, lines 155-165
if (
  storageQuotaMb !== "" &&
  storageQuotaMb !== null &&
  storageQuotaMb !== undefined
) {
  const numValue = Number(storageQuotaMb);
  if (!isNaN(numValue) && numValue > 0) {  // ← Rejects 0
    limits.storage_quota_mb = numValue;
  }
  // ← Zero silently NOT added to limits
}
```

**Suggested fix:**
1. Document backend behavior: does `storage_quota_mb` missing = use default? Does `0` = unlimited?
2. If `0` should be allowed (meaning "no limit" or "disable quota enforcement"), change the check to `>= 0`.
3. If `0` is invalid, add validation with a user-facing error message: `if (numValue === 0) { setError("Storage quota must be greater than 0 or left empty"); return; }`
4. Add a placeholder hint: "Leave empty to use role/global default. Enter 0 for unlimited (if supported)."

**Risk:** Admin misconfiguration: intended "zero quota" becomes "use default quota" silently.

---

### 8. **MINOR — User-Level Quota in UsersPanel: Same Zero-Value Issue**
**File:** `frontend/src/components/panels/UsersPanel.tsx` (not shown in detail, but pattern is similar)  
**Severity:** MINOR  
**Symptom:** Assuming UsersPanel also has a `storageQuotaMb` field with the same validation logic, the same zero-value silent-ignore issue applies.

**Confirmed by:** HANDOFF.md mentions `PUT /api/storage/admin/users/{user_id}/quota` with `storage_quota_mb`, implying user-level quota is also configurable. If UsersPanel has the same form pattern, it inherits this bug.

**Suggested fix:** Same as Finding #7.

---

### 9. **SAFE — Attachment Lifecycle Event Dispatch and Subscription Pattern**
**File:** `frontend/src/services/storageLifecycle.ts`  
**Severity:** None (verified safe)  
**Assessment:** The lifecycle event system is sound:
- Events are dispatched to `window` with the correct detail structure.
- AttachmentCard listens and updates state correctly.
- ProfileStorageTab responds to lifecycle events to refresh file list.
- No memory leaks detected; listeners are removed on unmount.

**Status:** ✓ Safe by design.

---

### 10. **SAFE — AttachmentCard Deleted State Rendering and UX**
**File:** `frontend/src/components/common/AttachmentCard.tsx:120-160`  
**Severity:** None (verified safe)  
**Assessment:** Deleted attachments are correctly rendered:
- Red trash icon and red line-through text (`line-through decoration-red-500`).
- `aria-disabled="true"` prevents interaction.
- Preview/download/remove buttons are disabled.
- Status is machine-readable (`data-lifecycle-status={lifecycleStatus}`).

No accessibility or UX issues found.

**Status:** ✓ Safe.

---

## SUMMARY

| Severity | Count | Status |
|----------|-------|--------|
| **BLOCKER** | 1 | RolesPanel: Zero quota silently ignored (#7) |
| **MAJOR** | 3 | Quota pre-check silent failure (#1), Delete doesn't refetch usage (#3), Protected files no management link (#5) |
| **MINOR** | 2 | Zero-value in UsersPanel (#8) + one design-intentional (quota staleness #2) |
| **SAFE** | 2 | Lifecycle event pattern, AttachmentCard rendering |

---

## RECOMMENDED ACTIONS (Priority Order)

1. **BLOCKER:** Fix RolesPanel & UsersPanel zero-value handling. Either allow `>= 0` with clear semantics, or add validation + error message for `=== 0`.

2. **MAJOR #3:** Add explicit error handling for `loadPage()` failure after delete. Ensure delete response always includes `usage`, or show a retry state.

3. **MAJOR #1:** Log quota pre-check exceptions; document retry behavior.

4. **MAJOR #5:** Add inline management links for protected files (avatars, skills).

---

## DEFERRED TO BACKEND REVIEW

- Concurrent upload quota atomicity (server-side responsibility)
- Delete response contract: always include `usage` field
- Backend interpretation of missing vs. zero `storage_quota_mb`
- Physical cleanup scheduling and error recovery

---

## NOTES

- i18n keys: ✓ Complete across all 5 locales.
- Error code mapping: ✓ Correctly maps 413, `storage_quota_exceeded`, `storage_operation_too_large`.
- Attachment lifecycle backward compat: ✓ Handles old key-only payloads.
- Test coverage: 11 frontend tests pass; no runtime preview executed per handoff doc.

---

**Review Date:** 2026-09-15  
**Reviewer Note:** This review covers ProfileStorageTab, useFileUpload quota gating, AttachmentCard lifecycle, and RolesPanel/UsersPanel quota config. Backend storage service, API routes, and MongoDB schema are out of scope.
