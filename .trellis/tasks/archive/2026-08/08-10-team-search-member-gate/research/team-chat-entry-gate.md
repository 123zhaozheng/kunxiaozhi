# Research: Team Chat Entry Gate

- Query: Trace Fast/Search/Team mode switching, team loading and selection, message send gating, and team creation/navigation entry points; identify the minimal UX needed for Team mode when no team exists or no team is selected.
- Scope: mixed (frontend primary, backend contracts/tests for compatibility)
- Date: 2026-08-10

## Findings

### Files found

- `frontend/src/components/chat/ChatInput.tsx`: owns textarea submit behavior, Team-mode placeholder/mention mode, and passes Team state to the toolbar/selectors.
- `frontend/src/components/chat/ChatInputToolbar.tsx`: loads a lightweight team count/name view, exposes the feature-menu Team selector, and renders the selected-team chip only when `selectedTeamId` is truthy.
- `frontend/src/components/chat/ChatInputSelectors.tsx`: mounts `AgentModeSelector` and `TeamPickerModal`; Team picker has create/manage navigation callbacks.
- `frontend/src/components/team/TeamPickerModal.tsx`: fetches up to 50 teams when opened, supports selection/clear/create/manage, and displays an empty list message.
- `frontend/src/components/chat/WelcomePage.tsx`: loads Team cards in Team mode, shows empty/loading/selection states, and links to `/team` for creation/management.
- `frontend/src/components/layout/AppContent/ChatAppContent.tsx`: owns the `useAgent` wiring, current mode/team state, session restoration, and the current send permission gate.
- `frontend/src/components/layout/AppContent/ChatView.tsx`: shares ChatInput props between the welcome and existing-message views.
- `frontend/src/hooks/useAgent.ts`: stores `currentAgent` and `selectedTeamId`, constructs submit requests, and exposes mode/team callbacks.
- `frontend/src/services/api/team.ts`, `frontend/src/types/team.ts`: typed team list/cache API and team response shape.
- `frontend/src/services/api/session.ts`: includes `team_id` in the submit body only when a non-empty ID is supplied.
- `frontend/src/components/team/TeamBuilderWrapper.tsx`: `/team` builder/list route, including empty-state create entry and use-team navigation back to chat.
- `frontend/src/components/chat/__tests__/teamMentionMode.test.ts`: current source-level regression explicitly expects Team mode to submit without an existing selection; this test will need replacement/update for the new requirement.
- `frontend/src/components/chat/__tests__/teamSelectorPlacement.test.ts`, `welcomeTeamGallery.test.ts`, `frontend/src/hooks/useAgent/__tests__/teamRequestScoping.test.ts`, `frontend/src/components/layout/AppContent/__tests__/teamRouteState.test.ts`: existing selector, welcome, request-scoping, and route-state coverage.
- `src/api/routes/chat_validation.py`, `src/agents/team_agent/nodes.py`, `tests/api/routes/test_chat_team_validation.py`, `tests/unit/agents/test_team_router.py`: backend currently permits missing `team_id` and deliberately treats it as a single-agent fallback.

### Current state transitions and send path

1. `useAgent` initializes `currentAgent` to an empty string and `selectedTeamId` to `null` (`frontend/src/hooks/useAgent.ts:70-82`). Agent discovery later resolves the current agent from `/api/agents`; the mode switch exposed to ChatInput is `switchAgent`, which only calls `switchAgentRaw` unless a persona-bound session locks it (`frontend/src/components/layout/AppContent/ChatAppContent.tsx:243-251`). The raw hook callback only sets `currentAgent` (`frontend/src/hooks/useAgent.ts:953-961`).
2. ChatAppContent passes `agents`, `currentAgent`, `onSelectAgent`, `selectedTeamId`, and `onSelectTeam` to ChatView (`frontend/src/components/layout/AppContent/ChatAppContent.tsx:887-895`). ChatView builds one shared ChatInput prop object for both the welcome view and the existing-message input (`frontend/src/components/layout/AppContent/ChatView.tsx:373-428`), then passes it to WelcomePage for zero messages (`frontend/src/components/layout/AppContent/ChatView.tsx:436-469`) or ChatInput at the bottom once messages exist (`frontend/src/components/layout/AppContent/ChatView.tsx:568-581`).
3. Agent mode selection is a generic list over every `agents` entry. Clicking a mode calls `onSelectAgent(agent.id)` and closes the selector (`frontend/src/components/selectors/AgentModeSelector.tsx:123-149`). There is no Team-specific selection requirement in this component.
4. When `currentAgent === "team"`, ChatInput switches mention search to `useTeamMentionSearch`, uses the Team placeholder, and handles Team mention selection through `onSelectTeam(team.id)` (`frontend/src/components/chat/ChatInput.tsx:165-185`, `frontend/src/components/chat/ChatInput.tsx:393-412`, `frontend/src/components/chat/ChatInput.tsx:632-645`, `frontend/src/components/chat/ChatInput.tsx:672-680`).
5. The actual form submit handler returns only for `!canSend`, then invokes `onSend` whenever the input has content and `canSubmit` is true (`frontend/src/components/chat/ChatInput.tsx:415-429`). `canSubmit` is `hasContent && canSend && !isLoading && !hasUploadingAttachment`; it does not inspect `currentAgent` or `selectedTeamId` (`frontend/src/components/chat/ChatInput.tsx:525-528`). The textarea is disabled only for the generic `disabled || !canSend` condition (`frontend/src/components/chat/ChatInput.tsx:672-680`).
6. ChatAppContent computes `canSendMessage` solely from `Permission.CHAT_WRITE` (`frontend/src/components/layout/AppContent/ChatAppContent.tsx:595-596`). Therefore a user with chat permission can type and submit in Team mode with no selected team, in both welcome and existing-message layouts.
7. `useAgent.sendMessage` derives `requestTeamId = currentAgent === "team" ? selectedTeamId : null` and passes it to `sessionApi.submitChat` (`frontend/src/hooks/useAgent.ts:671-691`). Session metadata stores `team_id` only when Team mode has a truthy selection (`frontend/src/hooks/useAgent.ts:738-753`, `frontend/src/hooks/useAgent.ts:786-801`). The API body similarly omits `team_id` when null/empty (`frontend/src/services/api/session.ts:159-205`).

### Existing Team selection and empty-list behavior

- Team mode exposes the Team selector through the feature menu (`frontend/src/components/chat/ChatInputToolbar.tsx:160-182`). The menu badge is the total team count loaded by a separate `teamApi.list(0, 50)` effect (`frontend/src/components/chat/ChatInputToolbar.tsx:102-121`, `frontend/src/components/chat/ChatInputToolbar.tsx:167-171`).
- A selected Team renders a removable toolbar chip and clearing it calls `onSelectTeam(null)` (`frontend/src/components/chat/ChatInputToolbar.tsx:225-245`). No replacement chip or inline warning is rendered when Team mode has no selection; the generic current-agent chip remains visible (`frontend/src/components/chat/ChatInputToolbar.tsx:183-191`).
- `ChatInputSelectors` mounts `TeamPickerModal` only in Team mode with `onSelectTeam`, passing `selectedTeamId ?? null` (`frontend/src/components/chat/ChatInputSelectors.tsx:182-203`). The modal fetches teams on open (`frontend/src/components/team/TeamPickerModal.tsx:33-41`), supports selecting or clearing a team (`frontend/src/components/team/TeamPickerModal.tsx:77-88`), and always provides New plus optional Manage buttons (`frontend/src/components/team/TeamPickerModal.tsx:142-189`).
- The modal's empty result state is informational text only: `team.noTeams` says to create a team, but the modal's New button is the actionable path (`frontend/src/components/team/TeamPickerModal.tsx:209-217`, `frontend/src/components/team/TeamPickerModal.tsx:144-157`). The picker does not auto-navigate or auto-create on empty data.
- The welcome page separately loads Team cards when Team mode is active (`frontend/src/components/chat/WelcomePage.tsx:157-180`). Before selection, it projects Team mentions/cards into the welcome surface (`frontend/src/components/chat/WelcomePage.tsx:294-316`). After a loaded empty list, it labels the section as empty and offers `navigate("/team")` with the `team.addNew` action (`frontend/src/components/chat/WelcomePage.tsx:335-355`, `frontend/src/components/chat/WelcomePage.tsx:418-465`). Existing teams render clickable cards calling `onSelectTeam(team.id)` (`frontend/src/components/chat/WelcomePage.tsx:554-564`).
- The full Team page has a matching empty-state create button (`frontend/src/components/team/TeamBuilderWrapper.tsx:568-600`). The page's Use action navigates back to Chat with both query parameters and route state (`frontend/src/components/team/TeamBuilderWrapper.tsx:286-296`); ChatAppContent consumes that request, switches to Team, selects the ID, and removes the query parameters (`frontend/src/components/layout/AppContent/ChatAppContent.tsx:345-362`).

### Backend contract and compatibility

- `AgentRequest.team_id` is optional (`src/kernel/schemas/agent.py:60-64`). `validate_team_agent_request` only strips persona/skill fields when a Team ID is present and intentionally allows a missing ID (`src/api/routes/chat_validation.py:6-14`; `tests/api/routes/test_chat_team_validation.py:8-17`, `56-68`).
- The Team agent runtime resolves an explicit team when `team_id` is supplied, but returns `None` when it is absent (`src/agents/team_agent/nodes.py:95-126`). Existing tests call this no-ID behavior a fallback and assert it remains (`tests/unit/agents/test_team_router.py:188-200`); the fallback prompt chooses Search when sandbox is active and Fast otherwise (`src/agents/team_agent/nodes.py:88-93`, `tests/unit/agents/test_team_router.py:203-217`).
- The route stores `team_id` in conversation metadata only for Team mode and only when truthy (`src/api/routes/chat.py:224-247`). This means the requested gate can remain frontend-only without changing the backend API or historical fallback contract; the UI simply must prevent the Team-mode send path from reaching it with no ID.

### Recommended complete minimal UX

1. Derive a Team-specific readiness value at the ChatInput/ChatView boundary: `teamSelectionRequired = currentAgent === "team" && !selectedTeamId`; combine it with permission/loading/upload readiness for `canSubmit` and disable the textarea/send action while it is true. Keep Fast/Search behavior unchanged.
2. Make the blocked Team state explicit and actionable in the existing picker surface. Reuse `TeamPickerModal` from the Team feature-menu action; when a Team mode switch occurs with no selection, open the picker or show a compact inline prompt near the input with a Select team action. The existing modal already supports selection, New, Manage, loading, search, and empty results.
3. For an empty list, preserve the existing `/team` route as the create entry. The welcome empty state already offers `team.addNew`; the picker already offers New and Manage. The blocked send state should point to that same create action rather than attempting a request or relying on the backend fallback.
4. On a valid Team selection, immediately re-enable the existing ChatInput submit path. Keep `useAgent` request scoping unchanged so `team_id` is sent only for Team mode and stored in session metadata.
5. Consider clearing `selectedTeamId` when switching away from Team only if the existing session/config behavior requires it; current persona-selection flow explicitly clears Team selection (`frontend/src/components/layout/AppContent/ChatAppContent.tsx:231-240`), while raw mode switching itself does not (`frontend/src/hooks/useAgent.ts:953-961`). This is separate from the send gate and should not be coupled unless tests establish a state-leak bug.

### Affected files and focused test additions

- Primary implementation candidates: `frontend/src/components/chat/ChatInput.tsx` (submit/readiness), `frontend/src/components/chat/ChatInputToolbar.tsx` or `ChatInputSelectors.tsx` (blocked-state affordance), and `frontend/src/components/chat/WelcomePage.tsx` only if the empty Team prompt needs to be tied to the same readiness signal.
- Existing integration wiring likely needs no shape change: `ChatAppContent.tsx` and `ChatView.tsx` already pass all needed mode/team callbacks and state.
- Add/update frontend tests for: Team + no selection disables submit/textarea and does not call `onSend`; Team + selected ID submits; Fast/Search remain sendable; empty Team list presents a create-team action; selecting a team from picker/welcome re-enables submission. Update/remove the stale assertion that Team mode can submit without a selection (`frontend/src/components/chat/__tests__/teamMentionMode.test.ts:34-37`).
- Existing source-level tests should remain relevant for Team selector placement, welcome cards, route selection, and request scoping (`frontend/src/components/chat/__tests__/teamSelectorPlacement.test.ts`, `frontend/src/components/chat/__tests__/welcomeTeamGallery.test.ts`, `frontend/src/components/layout/AppContent/__tests__/teamRouteState.test.ts`, `frontend/src/hooks/useAgent/__tests__/teamRequestScoping.test.ts`).
- Backend tests should remain unchanged unless the product decision also changes the backend fallback contract; current PRD scope calls for UI gating and explicitly says Fast/Search behavior should remain unaffected.

## Related specs

- `.trellis/spec/frontend/component-guidelines.md`: function components, Tailwind/theme variables, and shared component patterns.
- `.trellis/spec/frontend/hook-guidelines.md`: React hook side-effect/cleanup conventions.
- `.trellis/spec/frontend/state-management.md`: keep this selection local/prop-driven; API data uses typed service modules and in-memory TTL caching.
- `.trellis/spec/frontend/type-safety.md`: backend-mirroring snake_case API fields and typed API responses.
- `.trellis/tasks/08-10-team-search-member-gate/prd.md`: R1 restricts Team members to Search; R2 requires a selected Team before Team sends and a direct create entry when no teams exist.

## External references

- None used. Findings are based on the repository's current source, tests, and local Trellis specifications.

## Caveats / Not Found

- No runtime browser inspection was performed; behavior is inferred from source and existing source-level tests.
- The current frontend tests are mostly source-pattern tests rather than mounted component interaction tests, so the recommended readiness tests may require the project's established test harness or a small pure helper to make the gate directly testable.
- Team API calls are duplicated across the toolbar, picker, and welcome page. The list cache (`frontend/src/services/api/team.ts:14-66`) reduces duplicate network work, but each surface owns independent loading/error state.
- Backend intentionally supports Team mode without a Team ID as Fast/Search fallback. Do not remove that behavior as part of a frontend-only gate unless product requirements explicitly expand scope.
