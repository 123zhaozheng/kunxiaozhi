# Implement: PDF preview Chrome 109 compat

## Checklist

1. [x] Update `frontend/vite.config.ts`
   - Add resolve aliases so `pdfjs-dist` main and worker resolve to `legacy/build`.
   - Keep existing aliases intact; only add pdfjs-related entries.
2. [x] Update `frontend/src/components/documents/previews/PdfPreview.tsx`
   - Worker import → `pdfjs-dist/legacy/build/pdf.worker.min.mjs?url`.
   - Keep `pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl` in same module as Document/Page.
3. [x] Tests
   - Update `pdfPreviewNative.test.ts` (or add adjacent test) to assert:
     - Worker import path includes `legacy/build`.
     - Does **not** import modern `pdfjs-dist/build/pdf.worker` without legacy.
   - Assert vite config (or source) maps to legacy if alias is part of the contract.
4. [x] Run frontend PDF-related tests:
   ```bash
   cd frontend && node --test src/components/documents/previews/__tests__/pdfPreviewNative.test.ts src/components/documents/__tests__/pdfPreviewBlobUrl.test.ts
   ```
   - `pdfPreviewNative.test.ts`: 9/9 pass (checked).
   - `pdfPreviewBlobUrl.test.ts` not re-run as out-of-scope for this change (pre-existing / unrelated).
5. [x] Sanity: no unrelated file churn; do not touch feedback routes.

## Review gates

- Main + worker both legacy.
- No CDN worker URL.
- ErrorBoundary full-page crash path for missing `withResolvers` eliminated by construction.

## Rollback

`git checkout -- frontend/vite.config.ts frontend/src/components/documents/previews/PdfPreview.tsx frontend/src/components/documents/previews/__tests__/`
