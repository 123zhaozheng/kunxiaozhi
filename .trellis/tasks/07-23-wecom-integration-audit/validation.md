# Validation handoff

## Implemented

- WeCom session identity is scoped by `aibotid + chat_type + chat_id`.
- Unmapped WeCom users receive a visible error; raw enterprise userids are not persisted as application owners.
- WeCom and Web/PC now use the same Persona system prompt. No channel prompt or `_channel_context` is injected, preserving behavior and KV-cache parity.
- After a WeCom submission, the session stores the same Persona restore metadata as Web (`agent_id`, preset id/name/snapshot, project, and agent options), so reopening it no longer falls back to the base `fast`/`search`/`team` label.
- The sidebar virtually groups server-owned channel projects as `企微渠道 / Persona bot / conversation`, using compact navigation rows instead of cards or widgets.
- Channel projects and their conversations are read-only in the sidebar: no rename, icon edit, delete, drag/drop, manual child creation, or move target.
- Normal streaming replies use the Persona WeCom setting `segment_target_chars` (UI presets 300/500/600, default 600) together with the 2048 UTF-8 byte hard limit. Stream, non-stream, and timeout paths share the same boundary planner.
- Only the first `reveal_file` result is considered. Delivery requires an exact revealed-file index match for user, session, trace, key, and source.
- Connected status is refreshed by the owner reconcile heartbeat and resolves to disconnected after 60 seconds without a fresh update.
- Reconnect no longer returns false success on a non-owner API node; it returns HTTP 503.

## Automated evidence

- Focused follow-up backend tests: 24 passed.
- Broader WeCom/task/agent tests: 246 passed.
- Sidebar contract tests: 5 passed.
- Scoped Ruff and handler/persona Mypy: passed.
- Frontend TypeScript build, production build, and ESLint: passed.
- Configurable segment-target focused tests: 34 relevant backend tests and the frontend source contract passed; locale JSON parsing passed.
- Changed-source Mypy passed. Full-source Mypy still reports five unrelated pre-existing errors in sandbox, MinerU, and Dify modules.
- Full-repository Ruff and Mypy still report unrelated pre-existing issues; see final handoff.

## Real WeCom device checks still required

- Alternate messages between two `aibotid` values from the same enterprise user and confirm separate histories.
- Send a new message to each existing bot, then open its conversation in Web and confirm the Persona name/snapshot is restored instead of only `fast`, `search`, or `team`.
- Confirm the sidebar hierarchy is `企微渠道 / bot name / chat records` and its rows visually match Favorites/New Project.
- Compare the same Persona from Web and WeCom and confirm no channel-only instruction changes its behavior.
- Test single chat and group chat routing/mention policy.
- Select 300/500/600 characters in Persona WeCom settings, save, and verify the setting survives reload/hot reconnect.
- Generate long Chinese, English, Emoji, and Markdown replies for each target and verify approximate lengths, natural boundaries, bubble order, and the 2048-byte safety limit.
- Reveal one file, then multiple files, and verify only the first eligible file arrives.
- Kill/restart the owner process and verify connected becomes stale/disconnected, then reconnects.
- In a multi-node deployment, verify non-owner reconnect reports failure rather than success. Distributed forwarding to the owner is not implemented in this change.
