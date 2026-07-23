# WeCom Runtime and Delivery

> Executable contracts for persona routing, session identity, channel prompts, text segmentation, and outbound files.

## Scenario: WeCom run identity and outbound delivery

### 1. Scope / Trigger

- Trigger: any inbound WeCom message that creates or resumes a run, and any tool result considered for external file delivery.
- These rules prevent cross-persona history collisions, Web prompt pollution, orphaned user data, and arbitrary storage-key exfiltration.

### 2. Signatures

```python
def _wecom_session_key(aibotid: str, chat_type: str, chat_id: str) -> str: ...
def build_channel_prompt_section(channel_context: Any) -> str | None: ...

class RevealedFileStorage:
    async def owns_run_file(
        self, *, user_id: str, session_id: str, trace_id: str, file_key: str
    ) -> bool: ...

class WeComResponseCollector:
    def set_reveal_scope(
        self, *, user_id: str, session_id: str, trace_id: str
    ) -> None: ...
```

### 3. Contracts

| Contract | Required behavior |
|----------|-------------------|
| Session Redis key | `wecom:session:v2:{aibotid}:{chat_type}:{chat_id}` |
| Runtime owner | Resolve `UserStorage.get_by_username(sender_id).id`; never use a raw WeCom userid as a Mongo owner id |
| Run option | Set `agent_options["_channel_context"]` only on the current WeCom submission |
| Agent graphs | Fast, Search, and Team rebuild and consume the channel section per run |
| Tool event | Only `tool == "reveal_file"` may add a candidate |
| Selection | Retain at most the first candidate per run |
| Ownership query | Exact `user_id + session_id + trace_id + file_key + source="reveal_file"` match |
| Long text | UTF-8 byte-safe segments; finalize the original stream with segment 1, then send later segments serially with one bounded retry |

The channel context is never written to persona snapshots, session metadata, or memory. Web/PC runs omit it, so their rebuilt system prompt contains no WeCom section.

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Same chat opens another `aibotid` or changes single/group type | Different session key and session id |
| User mapping missing or lookup fails | Send a visible account error and stop before session/task creation |
| Channel context missing or `channel != wecom` | No delivery-channel prompt section |
| `reveal_project` or another tool returns a file-like dict | Ignore it |
| More than one `reveal_file` result | Keep the first; ignore and log later candidates |
| Reveal scope missing, index lookup fails, or ownership does not match | Fail closed before storage download/upload |
| Text exceeds 2048 UTF-8 bytes | Split at paragraph, line, sentence punctuation, space, then Unicode-safe hard boundary |

### 5. Good / Base / Bad Cases

- **Good**: user `u1`, session `s1`, trace `t1` reveals key `k1`; the same fields exist in `revealed_files`, so WeCom may upload it.
- **Base**: the model returns only text; no file storage or media upload path is entered.
- **Bad**: a tool returns a known S3 key from another trace; ownership lookup rejects it.
- **Bad**: storing WeCom instructions in a persona prompt; later Web runs inherit channel-only behavior.

### 6. Tests Required

| Test | Assertion point |
|------|-----------------|
| `tests/infra/agent/wecom/test_session_identity.py` | Different bot/chat-type inputs create different v2 keys and sessions |
| `tests/agents/core/test_channel_prompt.py` | WeCom gets the section; Web/missing context does not |
| `tests/infra/agent/wecom/test_collector_delivery.py` | Byte limit, exact text preservation, ordered independent segments, first-file selection |
| `tests/infra/test_revealed_file_storage.py` | Ownership query includes all five fields including `source` |
| Handler integration tests | Submit receives channel context; persisted trace is bound to collector; unmapped user is rejected visibly |

### 7. Wrong vs Correct

#### Wrong

```python
session_key = f"wecom:session:{chat_id}"
collector.add_file_to_reveal(any_tool_result)
await backend.download(file_key)
```

#### Correct

```python
session_key = _wecom_session_key(aibotid, chat_type, chat_id)
if tool_name == "reveal_file":
    collector.add_file_to_reveal(result)
if await revealed_files.owns_run_file(
    user_id=user_id, session_id=session_id, trace_id=trace_id, file_key=file_key
):
    await deliver(file_key)
```
