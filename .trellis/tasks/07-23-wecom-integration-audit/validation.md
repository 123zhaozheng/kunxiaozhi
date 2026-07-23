# Validation handoff

## Implemented

- WeCom session identity is scoped by `aibotid + chat_type + chat_id`.
- Unmapped WeCom users receive a visible error; raw enterprise userids are not persisted as application owners.
- A run-scoped WeCom delivery context is injected into Fast, Search, and Team prompts; Web/PC runs receive no WeCom section.
- Normal streaming replies are segmented by UTF-8 byte size and delivered as one finalized stream bubble followed by ordered proactive bubbles with bounded retry.
- Only the first `reveal_file` result is considered. Delivery requires an exact revealed-file index match for user, session, trace, key, and source.
- Connected status is refreshed by the owner reconcile heartbeat and resolves to disconnected after 60 seconds without a fresh update.
- Reconnect no longer returns false success on a non-owner API node; it returns HTTP 503.

## Automated evidence

- Focused WeCom/persona/status/reveal tests: 38 passed.
- Broader task/agent tests: 224 passed.
- Scoped Ruff: passed.
- Scoped Mypy for WeCom, revealed-file storage, and persona prompt: passed.
- Full-repository Ruff and Mypy still report unrelated pre-existing issues; see final handoff.

## Real WeCom device checks still required

- Alternate messages between two `aibotid` values from the same enterprise user and confirm separate histories.
- Test single chat and group chat routing/mention policy.
- Generate >2 KB Chinese/emoji/Markdown replies and verify bubble order and readability.
- Reveal one file, then multiple files, and verify only the first eligible file arrives.
- Kill/restart the owner process and verify connected becomes stale/disconnected, then reconnects.
- In a multi-node deployment, verify non-owner reconnect reports failure rather than success. Distributed forwarding to the owner is not implemented in this change.
