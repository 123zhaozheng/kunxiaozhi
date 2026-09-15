# Frontend Research: User Storage Management and Deleted Attachment State

**Task:** `.trellis/tasks/09-14-user-storage-management`
**Date:** 2026-09-14
**Scope:** Read-only frontend investigation. No product code was changed by this research pass.
**Frontend:** `frontend/` React 19 + TypeScript + Vite + Tailwind CSS, Web/PWA/Capacitor/Tauri targets.

## Executive findings

1. The existing personal-information surface is a single portal modal, `frontend/src/components/profile/ProfileModal.tsx`. It currently exposes seven tabs (`info`, `notification`, `preferences`, `envvars`, `tools`, `models`, `terms`) with separate mobile and desktop renderings. There is no storage/quota tab, route, API client, or storage-domain type yet.
2. Chat uploads already have a reusable pipeline in `hooks/useFileUpload.ts`: per-category size limits and per-draft file count come from `GET /api/upload/config`, images are compressed, a SHA-256 hash is checked for deduplication, upload progress is tracked, and the resulting `{key, url, name, type, mimeType, size}` is placed in a `MessageAttachment`. There is no aggregate per-user quota or remaining-space field in the frontend contract.
3. The same attachment object is submitted to chat and persisted in `user:message` events. `eventProcessor.convertAttachments()` and `historyLoader.ts` rehydrate it on later sessions. The attachment currently has no `deleted`, `deleted_at`, `available`, or lifecycle status field, so deleting the backing object cannot be represented faithfully in a historic message.
4. `AttachmentCard` is the common attachment UI for the draft composer and `UserMessageBubble`. Previewing goes through image viewers or `LazyDocumentPreview`; a missing object becomes a generic image fallback or `documents.failedToLoadFromS3`, not an explicit “deleted” state. The minimal compatible solution is to preserve the attachment metadata in messages, add a server-provided tombstone/status, and render the card as disabled with a non-color-only red strike/label rather than removing it.
5. `uploadApi.deleteFile()` already exists, but the current draft UI optimistically removes an attachment and ignores the response status. The backend can return `{deleted: false, status: "preserved"}` for a referenced/deduplicated object; the frontend must not claim that space was reclaimed in this case.
6. The frontend has several upload families that must not be conflated: chat/uploaded attachments, profile avatars, persona/team avatar uploads, Skills ZIP imports, Memory JSON import, and agent-generated `revealed_files`. A storage list needs an explicit scope/category contract, especially because persona/team code passes `folder: "persona-avatars"` to `uploadApi.uploadFile()` while the current ordinary upload route does not expose a typed folder response and keys are generated as `<category>/<user>/<id>.<ext>`.

---

## 1. Personal information/settings UI

### `ProfileModal.tsx`

**File:** `frontend/src/components/profile/ProfileModal.tsx`

Key locations:

- `:28-32` — `ProfileModalProps`; the modal is controlled by `showProfileModal` and `onCloseProfileModal`.
- `:34-45` — `TAB_ICONS` maps string tab keys to Lucide icons.
- `:55-63` — `activeTab` union; a new storage key must be added here.
- `:81-84` — active tab resets to `info` whenever the modal opens.
- `:108-118` — translated tab list. Current labels are `profile.title`, `profile.notifications`, `profile.preferences`, `envVars.title`, `profile.toolsTab`, `profile.modelIntro`, and `profile.termsTab`.
- `:120-129` — conditional tab-content renderer.
- `:164-241` — mobile bottom sheet: `max-h-[90dvh]`, horizontal overflow tab strip, scrollable content, footer with `safe-area-bottom`.
- `:243-314` — desktop centered modal: `w-[80vw] max-w-[680px] h-[75dvh]`, fixed `152px` sidebar, independently scrollable right content.
- `:315-317` — portal target is `document.body`.

The desktop and mobile tab buttons are duplicated rather than shared. A storage tab must be inserted into both through the shared `tabs` array, not by creating a one-off mobile/desktop branch. The existing mobile strip automatically scrolls the active button into view (`:72-79`), which is useful for a longer tab name. The fixed desktop sidebar is narrow; use a short label such as “Storage”/“空间” and verify all five locales do not wrap badly at `152px`.

Suggested smallest integration:

```text
ProfileModal.tsx
  activeTab union + TAB_ICONS + tabs + renderTabContent
      └── ProfileStorageTab.tsx
          ├── storageApi.getSummary/list
          ├── quota progress/warning
          ├── file selection and bulk delete
          └── ConfirmDialog
```

Recommended icon: `HardDrive` or `Database` from `lucide-react`, consistent with the existing icon-only chrome. A separate top-level route is unnecessary for the requested interaction.

### Existing profile tab patterns to reuse

- `ProfileInfoTab.tsx` (`frontend/src/components/profile/tabs/ProfileInfoTab.tsx`) already uses `useTranslation`, `useAuth`, `authApi`, `uploadApi`, local async state, toast success/error, and the shared theme classes. It contains the user avatar upload/delete flow, which is a protected profile asset and should be classified separately from chat files unless the backend summary explicitly includes it.
- `ProfileEnvVarsTab.tsx` is the closest list-management pattern: `useEffect` + `useCallback` fetch, `SkeletonList`, `LoadingSpinner`, `toast`, permission gating, and `ConfirmDialog` for deletion. A storage tab can follow this pattern but should use server pagination/selection rather than rendering an unbounded list.
- `ProfilePreferencesTab.tsx` demonstrates a compact card, theme-aware rows, portal dialogs, and local preference state.
- `ProfileNotificationTab.tsx` demonstrates inline status/error text, including an icon and translated state, rather than relying only on a toast.
- `ProfilePasswordTab.tsx` uses visible red/green inline feedback, but it is currently not imported/rendered by `ProfileModal`; do not assume every profile tab file is active.

### Modal accessibility baseline and risks

The current modal has useful behavior (body-scroll lock, Escape close, outside-click close, mobile swipe-to-close), but the tab buttons are plain buttons without explicit `role="tab"`, `aria-selected`, or `aria-controls`. The close button renders only `<X>` without an `aria-label`. The modal shell has no explicit `role="dialog"`/`aria-modal`/labelled heading. A storage tab can follow current conventions for minimal scope, but a quality implementation should at least add:

- a translated accessible label to both close buttons;
- `role="tablist"` on each tab strip/sidebar, `role="tab"` and `aria-selected` on each tab, and `role="tabpanel"`/`aria-labelledby` for active content;
- keyboard activation/focus behavior consistent with the existing buttons;
- a real accessible progressbar and checkbox labels in the storage list.

Do not use red color alone to communicate quota or deletion. Pair it with text, icon/strike treatment, and `aria-label`/`title`.

### Host and entry point

`frontend/src/components/layout/AppContent/AppShell.tsx:281-287` renders `ProfileModal`. `ChatAppContent.tsx` owns the boolean and passes it through the shell; `Header.tsx`/`UserMenu` calls `onShowProfile`. The storage tab therefore works from all chat sessions without adding a new router entry.

---

## 2. Existing API clients and frontend data types

### Upload API

**File:** `frontend/src/services/api/upload.ts`

Existing operations:

- `uploadFile(file, folderOrOptions)` — XHR `POST ${API_BASE}/api/upload/file?folder=...`; supports progress and abort. The response is normalized to `UploadResult`.
- `checkFile(hash, size, name, mimeType)` — `POST /api/upload/check` for content-hash deduplication.
- `uploadAvatar(file)` / `deleteAvatar()` — dedicated avatar endpoints.
- `getConfig()` — cached once in `_configPromise`; returns `UploadConfig` with provider and per-category limits.
- `getSignedUrl(key)` / `getSignedUrls(keys)` — private-file access URLs, resolved through `getFullUrl`.
- `deleteFile(key)` — `DELETE /api/upload/{key}` and returns `{deleted, key}` in the current declared type, even though the backend may also return a `status` such as `preserved` or `deleting`.

Important implementation mismatch: `uploadFile()` parses XHR errors manually and throws a plain `Error`; it does not reuse `authFetch`/`ApiRequestError` structured `code`/`detail` handling. Quota rejection should use a stable backend error code and either extend this XHR parser or route preflight/upload through a shared typed error helper. Otherwise the user will see a raw backend string and cannot reliably distinguish “quota full” from network/auth failure.

The module-level `getConfig()` cache has no invalidation method. If quota usage is added to that response, it will become stale after upload/delete unless the client explicitly invalidates/refetches or the new storage summary lives in a separate non-sticky endpoint.

### Current types

**File:** `frontend/src/types/upload.ts`

```typescript
export interface MessageAttachment {
  id: string;
  key: string;
  name: string;
  type: FileCategory;
  mimeType: string;
  size: number;
  url?: string;
  uploadProgress?: number;
  isUploading?: boolean;
}

export interface UploadConfig {
  enabled: boolean;
  provider?: string;
  uploadLimits: {
    image: number;
    video: number;
    audio: number;
    document: number;
    maxFiles: number;
  };
}
```

`UploadResult` and `FileCheckResult` carry only object metadata. There is no user aggregate usage, quota, deletion status, owner, reference count, or pagination type.

**File:** `frontend/src/types/auth.ts`

- `Permission` has `file:upload`, category-specific upload permissions, and `avatar:upload`, but no storage-read/storage-delete permission.
- `RoleLimits` has per-file MB limits and `max_files` (per upload), but no aggregate bytes/file-count quota.
- `User.metadata` is unrelated preference metadata; do not store live quota there.

**File:** `frontend/src/services/api/auth.ts`

`authApi.getCurrentUser()`/`getProfile()` expose profile data but no storage summary. `frontend/src/services/api/user.ts` is admin user CRUD, not a current-user storage client.

**Recommended new contract location:** add a dedicated `frontend/src/services/api/storage.ts` (or extend `upload.ts` only if the backend calls the feature “upload storage”) and export it from `services/api.ts`. Keep storage-specific response types co-located with the API module, matching the repository type-safety guideline.

Suggested response shape (server-authoritative and byte-based):

```typescript
export interface StorageSummary {
  used_bytes: number;
  quota_bytes: number | null; // null = unlimited
  files_count: number;
  warning_threshold_bytes: number | null;
  blocked: boolean;
  can_upload: boolean;
}

export interface StorageFile {
  id: string;
  key: string;
  name: string;
  category: FileCategory;
  mime_type: string;
  size: number;
  created_at: string;
  last_referenced_at?: string | null;
  reference_count?: number;
  status: "available" | "deleted" | "preserved";
  deleted_at?: string | null;
  url?: string | null;
}
```

The exact names must follow the backend implementation. The key compatibility requirement is that history attachment payloads expose either `status/deleted_at/available` or a stable lookup result so the UI does not guess from a generic HTTP 404.

---

## 3. Upload entry points and current behavior

### Active chat path

**Files:**

- `frontend/src/hooks/useFileUpload.ts`
- `frontend/src/components/chat/ChatInput.tsx`
- `frontend/src/components/chat/ChatInputToolbar.tsx`
- `frontend/src/components/chat/ChatInputAttachments.tsx`
- `frontend/src/components/common/AttachmentCard.tsx`
- `frontend/src/components/layout/AppContent/useDragAndDrop.ts`
- `frontend/src/hooks/usePasteHandler.tsx`

`useFileUpload.ts` details:

- Fetches `uploadApi.getConfig()` once per hook (`:63-93`).
- `validateSize()` uses category MB limits (`:95-111`); while config is loading, it permits the file (`if (!uploadLimits) return true`).
- `validateCount()` checks only current draft attachment count against `maxFiles` (`:113-129`), not aggregate storage usage.
- Compresses images before hashing/upload (`:146-160`), then creates a temporary uploading attachment with progress.
- Calls `checkFile()` using worker-computed SHA-256; existing dedupe records become a normal final attachment (`:197-224`).
- Uploads with progress/abort and turns the response into `MessageAttachment` (`:226-274`).
- Errors toast and remove the temporary draft attachment. This is the best place for a fast client-side “quota would be exceeded” check, but the server must remain authoritative because deduplication and concurrent uploads make a local sum approximate.

`ChatInputToolbar.tsx:99-159` is the active file picker path used by `ChatInput`; it sets the category-specific `accept` filter and calls the `uploadFiles` callback. `ChatInput.tsx` also receives files from drag/drop and paste. Long pasted text over `PASTE_TEXT_THRESHOLD = 3000` is converted into a generated `.md`/`.txt` upload by `usePasteHandler.tsx`.

`useDragAndDrop.ts` installs document-level drag/drop listeners. It stores draft attachments in `ChatAppContent.tsx` as `pageDragAttachments`, and passes that state down to `ChatView`/`ChatInput`. Global drops are ignored in surfaces with `[data-disable-global-file-drop='true']`.

### Draft attachment cards and draft deletion

**File:** `frontend/src/components/chat/ChatInputAttachments.tsx`

- Uses `AttachmentCard` in `variant="editable"`, `size="compact"`.
- Clicking images opens `ImageViewer`; other files use `openAttachmentPreview()` and the global preview host.
- Removing a completed draft immediately filters it locally and calls `uploadApi.deleteFile(attachment.key)` without confirmation and without inspecting `{deleted, status}`.
- Cancelling an in-progress upload uses the abort map from `useFileUpload`.

For quota management, preserve this fast draft behavior, but handle a delete response of `preserved`/`deleting` explicitly and publish a cache/event invalidation so the storage tab refreshes. A failed cleanup should not silently imply that bytes were reclaimed.

`FileUploadButton.tsx` is another reusable category picker with the same `useFileUpload` integration, but it has no current call site in `frontend/src` (the active chat path is `ChatInputToolbar`). Keep it aligned if it is revived, or avoid adding quota logic only to the unused component.

### Message submission and clearing

**Files:**

- `frontend/src/components/chat/ChatInput.tsx:500-557`
- `frontend/src/components/layout/AppContent/ChatView.tsx:394-403`
- `frontend/src/components/layout/AppContent/ChatAppContent.tsx:899-907`
- `frontend/src/hooks/useAgent.ts:575-691`
- `frontend/src/hooks/useAgent/optimisticMessages.ts:20-51`
- `frontend/src/services/api/session.ts:160-210,403-440`

The composer snapshots `draftAttachments`, calls `onSend`, and clears input/attachments only after the submit is accepted. If admission fails with the known sandbox-capacity error, it restores the exact draft. This same acceptance boundary is appropriate for quota errors: a rejected upload/send must leave the file draft available while presenting a translated cleanup CTA.

`sessionApi.submitChat()` includes `attachments` in the JSON body. `createOptimisticMessagesForSend()` retains the attachment array on the optimistic user message. The attachment is therefore not merely a transient upload-card concern; once accepted it belongs to message history.

### Other upload-like surfaces

1. **Profile avatar:** `ProfileInfoTab.tsx` uses `uploadApi.uploadAvatar()`/`deleteAvatar()`, with client image compression and `avatar:upload` permission. The backend avatar namespace is owned separately. Prefer excluding avatars from the user-cleanup list or displaying them as protected assets; deleting the current avatar from a generic file list could break the profile.
2. **Persona editor:** `components/persona/PersonaEditorModal.tsx:529-550` calls `uploadApi.uploadFile(compressed, {folder: "persona-avatars"})`.
3. **Team builder:** `components/team/TeamBuilder.tsx:314-328` makes the same `folder: "persona-avatars"` call.
4. **Skills ZIP:** `components/panels/SkillsPanel/ZipUploadModal.tsx` and `services/api/skill.ts:249-287` use skill-specific `/skills/upload/preview` and `/skills/upload` multipart endpoints. These are not ordinary chat attachment objects and should not appear in a personal upload list unless the product explicitly chooses to unify storage.
5. **Builtin Skills ZIP:** `BuiltinSkillsPanel.tsx` uses `builtinSkillApi.previewZip`/upload endpoints, also separate.
6. **Memory JSON import:** `MemoryPanel/index.tsx:286-292` accepts a local JSON file for import; it is not an S3 upload by itself.
7. **Revealed files:** `components/fileLibrary/RevealedFilesPanel.tsx`, `useRevealedFiles.ts`, and `services/api/revealedFile.ts` represent agent-generated `reveal_file`/`reveal_project` artifacts. They have their own `file_key`, `session_id`, `source`, stats, favorites, and grouped list endpoints, but no delete operation in the frontend. This file library is a separate UX and should not be silently substituted for user-upload storage. Decide explicitly whether quota counts generated artifacts; if it does, provide a separate source/category and deletion semantics.

Compatibility warning: the ordinary `uploadApi.uploadFile()` call appends a `folder` query string, but the current frontend type and observed ordinary backend route do not return a folder/purpose field. Persona/team avatar uploads can therefore be indistinguishable from normal category uploads in a generic storage list. The backend should add an explicit purpose/ownership contract before exposing bulk deletion.

---

## 4. Chat message attachment lifecycle and preview chain

### Backend-to-frontend conversion

**File:** `frontend/src/hooks/useAgent/eventProcessor.ts:40-63`

`convertAttachments()` expects backend objects with snake-case `mime_type` and maps them to `MessageAttachment` (`mimeType`). It currently copies only `id`, `key`, `name`, `type`, `mime_type`, `size`, and `url`. Add optional deletion/status fields here with backward-compatible defaults (`available`/`status` absent means the current available behavior).

**Files:**

- `frontend/src/hooks/useAgent/eventHandlers.ts:413-489` — live `user:message` event; calls `convertAttachments(data.attachments)` and attaches the result to the user message.
- `frontend/src/hooks/useAgent/historyLoader.ts:422-451` — historic `user:message` event; same conversion and preservation in reconstructed messages.
- `frontend/src/hooks/useAgent/types.ts:87-97` — `EventData.attachments` currently has the narrow no-status shape.
- `frontend/src/types/message.ts:7-31` — `Message.attachments?: MessageAttachment[]`.
- `frontend/src/types/session.ts:19-28` — historic session message additional kwargs contain parts but do not define an attachment status contract.

This is the critical seam for “delete the object, keep the message UI.” A storage delete must not remove the attachment from `user:message` history; it should update the attachment’s lifecycle state or make a stable lookup result available during conversion.

### Card and preview chain

**File:** `frontend/src/components/common/AttachmentCard.tsx`

- Default mode is the user-message card (`:180+`); compact mode is the composer card (`:70+`).
- Images use `ImageWithSkeleton` only in compact mode; default mode uses a direct `<img>` with no `onError` branch.
- Non-image cards open the global attachment preview through `AttachmentPreviewHost`.
- `AttachmentCard` currently has no deleted/disabled prop and always renders a clickable `<button>` in default mode.

**Files:**

- `components/chat/ChatMessage/UserMessageBubble.tsx:43-75` — renders every message attachment and opens image gallery/document preview.
- `components/chat/AttachmentPreview.tsx` — older compact list with remove/cancel controls and no deleted state; check before touching if it becomes active again.
- `components/chat/AttachmentPreviewHost.tsx` — reads `attachmentPreviewStore` and passes `name`, `key`, `size`, `mimeType`, and image URL into `LazyDocumentPreview`.
- `components/documents/LazyDocumentPreview.tsx` → `DocumentPreview.tsx` → `useDocumentPreviewState.ts`.
- `useDocumentPreviewState.ts:315-434` resolves a signed/proxy URL and fetches bytes/text; all failures become the translated generic `documents.failedToLoadFromS3`.
- `DocumentPreviewContent.tsx:135-151` displays a red generic error icon/message but does not know whether the file was deleted, unauthorized, expired, or temporarily unavailable.
- `ImageWithSkeleton.tsx` handles image load errors with the file name, but the default `AttachmentCard` image bypasses it.

Recommended UI behavior:

- If a server-provided status is deleted, render the attachment card in a noninteractive/disabled state: red border or translucent red overlay, a visible diagonal strike/line across the icon/name, and a translated `已删除`/`Deleted` badge. Keep filename, type, and size so the user can identify the old input.
- Do not let the card open an image/document preview when deleted. Do not rely only on CSS `line-through`; combine the strike with text and `aria-label`.
- If the status is unknown and a preview fetch returns a stable `file_deleted`/410 response, show the same deleted state in the preview panel and offer a re-upload action or close action. Do not classify every 404 as deleted: signed URL expiration, auth failures, network errors, and a genuinely missing object are different states.
- If the user removes a file from the storage tab while a draft still references it, either remove that draft attachment through a storage-deletion event or mark it deleted and prevent submission with a translated re-upload prompt. Historic messages remain unchanged except for the tombstone.

---

## 5. Recommended minimum storage-tab design

### Data flow

Add a typed `storageApi` with:

```text
getSummary()                        GET /api/storage/me (or agreed upload route)
listFiles({cursor, limit, q, category}) GET ...
removeFiles(keys or ids)            DELETE ... / batch endpoint
```

The response should be user-scoped server-side and should distinguish:

- current bytes used and quota bytes (or unlimited);
- warning threshold and hard-block state;
- file count and optional category totals;
- whether a deletion actually reclaimed the object (`deleted`, `preserved`, `deleting`);
- reference/dependency state for deduplicated objects;
- lifecycle metadata needed by historic attachments (`status`, `deleted_at`, `available`).

Do not fetch all files and compute quota in React. Do not use the existing `revealedFileApi.getStats()` as a substitute; it is a different artifact domain.

### Tab layout

`ProfileStorageTab.tsx` should follow the profile card language:

1. **Summary card:** title/icon, “used / quota” formatted from bytes, progress bar, file count. Use `role="progressbar"`, `aria-valuenow`, `aria-valuemin`, `aria-valuemax`, and a translated label. For unlimited quotas, render an explicit “unlimited” state rather than a 100% bar.
2. **Threshold banner:** normal, warning, and blocked states. The threshold should come from the backend, not a hardcoded frontend percentage. The warning copy should tell the user what action to take (“清理不再使用的文件” / “Clean up files”). A blocked state should say that text chat remains available if that is the product decision, while new file uploads are blocked.
3. **Toolbar:** refresh, search/type filter if server supports it, select-all for the current page, selected-byte count, and batch delete action. Every icon-only action needs a translated `aria-label`/`title`.
4. **File list:** filename, category/MIME, size, created/last-used time, status/protection/reference badge, checkbox, and optional per-row delete action. Use a compact mobile list rather than forcing a wide table inside the `90dvh` bottom sheet.
5. **Empty/loading/error:** use `SkeletonList`/`PanelLoadingState`, an explicit empty state, and inline retry plus toast for mutation failures.
6. **Confirmation:** use the existing `ConfirmDialog` with a danger variant. Explain that deleting a file removes future access but old messages remain visible as deleted; explain preserved/reference behavior if the API says it cannot be physically reclaimed yet.

Use `formatFileSize` from `components/common/AttachmentCard.tsx` (or move it to a neutral utility if the new tab needs it without coupling to a component). Keep bytes as integers in API state to avoid MB rounding errors.

### Aggregate quota and upload blocking

Current `useFileUpload.validateSize()` only validates each selected file. Extend the upload contract in two layers:

1. **Fast client check:** use a current summary snapshot to reject an obvious `used_bytes + selected_bytes > quota_bytes` before hashing/uploading. Include dedupe caveat in code: a repeated hash may not consume new bytes, so this is only an early UX check.
2. **Authoritative server check:** upload/preflight endpoint returns a stable `storage_quota_exceeded` code with `used_bytes`, `quota_bytes`, and optionally `required_bytes`. The XHR parser must preserve that code and `translateBackendError`/i18n must map it in all locales.

When the quota reaches the warning threshold, show a non-blocking warning in the storage tab and, if desired, a once-per-session compact warning near the chat upload controls. At the hard limit, disable/reject file selection/upload but leave ordinary text chat usable. Preserve the draft text/files only when rejection occurs before a file has actually been deleted; show a direct “Open storage management” CTA if the UI can navigate/select the profile modal, otherwise give clear instructions.

### Deletion semantics and agent continuity

The desired behavior requires two separate concepts:

- **Physical storage cleanup:** reclaim bytes only when no other user/message/reference depends on the object.
- **Message attachment tombstone:** retain the historical metadata and show it as deleted to the user, so the agent cannot silently receive a broken URL.

The frontend should honor the backend’s result rather than assuming a `DELETE` means physical deletion. A response such as `status: "preserved"` should update the list/status and explain why bytes did not drop. A true deleted response should invalidate summary/list caches and broadcast a deletion event to open chat surfaces.

For agent re-upload guidance, the best frontend contract is a stable `available`/`status` field in the `user:message` attachment event plus a stable `file_deleted` response from the file-fetch/tool path. Then the UI can say “This attachment was deleted; upload it again to let the agent access it” without treating unrelated network errors as deletion.

---

## 6. Responsive, theme, and native-runtime considerations

- `ProfileModal` mobile is a bottom sheet (`sm:hidden`) with `max-h-[90dvh]`; the storage list needs one scroll owner and should not create an unscrollable nested list inside the already scrollable content unless the list height is bounded.
- Desktop uses `w-[80vw] max-w-[680px]`; a quota card plus file list should fit in the right pane at the existing `p-5 sm:p-8` padding. Avoid fixed-width columns and expose filename truncation with a tooltip/title.
- All existing profile surfaces use `dark:` Tailwind variants and stone/amber theme classes. New red warning/deleted styles need dark variants (`dark:bg-red-900/30`, `dark:text-red-300`, etc.) and sufficient contrast.
- Mobile and native builds rely on `safe-area-bottom` and dynamic viewport units. The existing footer already adds safe-area padding; do not place a fixed bottom action bar below it without accounting for the inset.
- Use the same `ConfirmDialog` portal/z-index (`z-[300]`) carefully: the profile modal itself is also `z-[300]`. Existing nested dialogs work through portals, but focus/body-scroll interactions should be tested.
- Tab labels are translated into English, Simplified Chinese, Japanese, Korean, and Russian. The i18n loader (`frontend/src/i18n/index.ts`) statically imports `en.json`, `zh.json`, `ja.json`, `ko.json`, and `ru.json`, with English fallback. Add storage keys to all five locale files; do not rely on a missing-key fallback for visible status labels.

---

## 7. Tests and verification targets

### Existing relevant tests

The frontend has no `test` script and does not list Vitest in `frontend/package.json`; tests use Node’s test runner with `tsx`/TypeScript imports and source-level assertions.

- `frontend/src/components/profile/tabs/__tests__/profileAppNotificationSource.test.ts` — current profile-tab source-test style. Add a storage-tab source/behavior test alongside it, or use a pure storage utility test if DOM rendering is not set up.
- `frontend/src/components/chat/ChatMessage/__tests__/userMessageBubble.test.ts` — source assertions around user-message attachment rendering; extend for deleted-card non-preview behavior.
- `frontend/src/components/chat/__tests__/AttachmentPreviewHost.test.ts` and `attachmentPreviewStore.test.ts` — global preview-host/store contracts.
- `frontend/src/hooks/useAgent/__tests__/eventProcessor.test.ts` — pure event transformation tests; add a deleted/available attachment mapping case.
- `frontend/src/hooks/useAgent/__tests__/historyLoader.test.ts` — historic `user:message` reconstruction; add a case proving tombstone metadata survives replay.
- `frontend/src/hooks/useAgent/__tests__/eventHandlers.userMessageTimestamp.test.ts` — live user-message handler coverage.
- `frontend/src/services/api/__tests__/session.test.ts` — `buildSubmitChatBody()` already asserts `attachments` field shape.
- `frontend/src/components/layout/AppContent/__tests__/globalFileDropGuards.test.ts` — global drop opt-out behavior.
- `frontend/src/components/fileLibrary/__tests__/utils.test.ts` — file-card/preview testing style, but this is the separate revealed-file domain.
- `frontend/src/i18n/__tests__/*.test.ts` — locale key contract/source tests; add a parity test that required storage keys exist in all five JSON files.

### New or updated test cases required by the feature

1. **API contract:** storage summary/list/delete URL/params; pagination; batch result handling; `deleted` vs `preserved`; quota error code parsing and translation.
2. **Upload gate:** obvious over-quota selection is rejected before network upload; server `storage_quota_exceeded` leaves the draft intact; ordinary text submission remains possible if that is the agreed UX.
3. **Quota formatting/progress:** zero, unlimited, exactly-at-limit, over-limit, warning, and byte rounding cases; progressbar ARIA values.
4. **Deletion UI:** select-all/partial selection, confirmation loading/cancel, successful refresh, preserved/reference status, and failed delete. Verify dark/mobile class variants and accessible labels if using source tests.
5. **Attachment lifecycle:** `convertAttachments()` preserves `status`/`deleted_at`; historic reconstruction preserves the attachment; deleted `AttachmentCard` has visible label/strike and does not invoke preview.
6. **Preview errors:** stable deleted/410 response renders the translated deleted state; generic network/401/expired-signed-url remains a generic load error rather than a false deleted label.
7. **Cross-surface invalidation:** a storage deletion refreshes summary/list and does not erase already-rendered historical message metadata; an open draft cannot submit a deleted object without a re-upload prompt.

Useful commands after implementation (from `frontend/`):

```bash
pnpm exec tsx --test src/services/api/__tests__/storage.test.ts
pnpm exec tsx --test src/hooks/useAgent/__tests__/eventProcessor.test.ts src/hooks/useAgent/__tests__/historyLoader.test.ts
pnpm run build
pnpm run lint
```

The repository’s root `make test` is backend pytest-only (`Makefile:128-130`), so it does not replace frontend checks.

---

## 8. Compatibility and accessibility risks to resolve before implementation

- **Dedupe/reference safety:** ordinary uploads are content-hash deduplicated; deleting a key may preserve it for referenced messages/users. The UI must render the actual server result and must not show reclaimed bytes prematurely.
- **Ownership/security:** `deleteFile(key)` is key-based. The backend must enforce current-user ownership/reference authorization; the frontend cannot safely compensate for an overbroad delete endpoint.
- **Stale config:** `_configPromise` is permanently cached. Do not put mutable aggregate usage only in `UploadConfig` without invalidation.
- **XHR error shape:** upload XHR errors bypass `ApiRequestError` and stable code translation. Add a structured error path before relying on quota-specific UX.
- **Signed URL/404 ambiguity:** a missing image or generic S3 fetch failure is not necessarily a user deletion. Prefer explicit status/tombstone or a stable 410/code.
- **Historical compatibility:** old event payloads have only `mime_type` and URL metadata. New fields must be optional, and old attachments should continue to render as available unless a lookup says otherwise.
- **Folder/purpose ambiguity:** persona/team avatar callers pass `folder`, but the current ordinary upload contract does not make that purpose visible. Clarify server-side inclusion/exclusion before enabling bulk cleanup.
- **Generated artifacts:** `revealed_files` have a different schema and no frontend delete API. Decide whether they count in quota rather than accidentally mixing them with upload records.
- **Current draft deletion:** `ChatInputAttachments` ignores delete response status. Update it only after the new response contract is defined; otherwise a preserved object can be misreported as removed.
- **Direct image element:** default message cards use `<img>` without an error state. A status field should drive deleted rendering before opening the image; add an error fallback for unknown status.
- **Keyboard/focus:** selection checkboxes, row delete buttons, tab buttons, and confirmation dialog must be keyboard reachable. Do not hide essential actions behind hover-only opacity on mobile.
- **Color contrast:** red/amber quota states need text/icon labels and dark-mode contrast. A red strike alone does not satisfy the user’s “deleted” requirement accessibly.
- **Nested portals/scroll:** `ProfileModal` and `ConfirmDialog` share the same z-index/portal pattern and both manipulate body overflow. Verify cancel/close restores scroll and focus on both desktop and mobile.

## Recommended implementation slice

1. Agree backend scope/contract first: user-scoped storage summary/list, byte quota and warning threshold, safe batch delete response, reference-preserving semantics, and attachment tombstone fields.
2. Add `storageApi` and types plus stable quota/deleted error mapping; keep `uploadApi.getConfig()` for static per-file limits and invalidate any mutable quota cache.
3. Add `ProfileStorageTab` through the existing `ProfileModal` tab array, with summary/progress/banner/list/selection/confirmation/loading/error states.
4. Add the aggregate upload preflight/gate in `useFileUpload` and server-error handling while retaining draft state on rejection.
5. Extend `MessageAttachment`, `EventData`, `convertAttachments`, and history/live event handling with optional deletion state.
6. Teach `AttachmentCard`/`UserMessageBubble`/`DocumentPreviewContent` the disabled deleted presentation and stable preview error state.
7. Add the focused tests above, then verify mobile/dark/native-safe-area behavior against the running app.

This keeps the first UI slice small and consistent with existing profile and attachment components while preserving historical messages and preventing a deleted URL from being silently presented as a usable agent input.
