# Personal Knowledge Base (frontend)

Code: `frontend/src/components/knowledge/`, API client `frontend/src/services/api/knowledgeBase.ts`.

- Route `/knowledge/:kbId?`, sidebar entry and settings tab are shown only when `KB_ENABLED` is true (via `SettingsContext`).
- Pages: `KnowledgeBaseListPage` → `KnowledgeBaseDetail` with tabs Documents / Retrieval test / Settings. Document preview uses `DocumentDetailSidebar` (content + chunks with line numbers).
- Live status: `useAdaptivePolling` polls fast while tasks are active and backs off when idle; `useWebSocket` dispatches KB events through `utils/knowledgeBaseEvents.ts` so lists refresh without polling.
- Uploads go through `useKbUploadQueue` (per-file progress, duplicate handling). Backend error codes are mapped in `utils/backendErrors.ts`; add new KB codes there.
- `index_outdated` shows a notice prompting the owner to rebuild the index.
- All strings live under `knowledge.*` in `i18n/locales/{zh,en}.json`; `i18n/__tests__/knowledgeKeys.test.ts` enforces key parity.
- Tests are `node:test` via `tsx --test` (`components/knowledge/__tests__/`); keep pure logic in `knowledgeUtils.ts` so it stays testable.
- Graph tab (`components/knowledge/graph/`): React Flow canvas is lazy-loaded; layout is the pure, deterministic `forceLayout.ts` (≤500 nodes); data→node/edge mapping lives in `graphMapping.ts`. Retrieval test has a "graph boost" toggle and per-hit source labels. Strings: `knowledge.graph.*`.
