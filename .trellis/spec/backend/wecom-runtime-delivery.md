# WeCom Runtime and Delivery

> Executable contracts for persona routing, session identity, cross-channel prompt parity, text segmentation, and outbound files.

## Scenario: WeCom run identity and outbound delivery

### 1. Scope / Trigger

- Trigger: any inbound WeCom message that creates or resumes a run, and any tool result considered for external file delivery.
- These rules prevent cross-persona history collisions, Web prompt pollution, orphaned user data, and arbitrary storage-key exfiltration.

### 2. Signatures

```python
def _wecom_session_key(aibotid: str, chat_type: str, chat_id: str) -> str: ...
async def _persist_wecom_session_config(
    *,
    session_id: str,
    run_id: str,
    agent_id: str,
    agent_request: Any,
    agent_options: dict[str, Any],
    project_id: str | None,
) -> bool: ...

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
| Prompt parity | Do not inject channel-specific system prompts or `_channel_context`; Web and WeCom use the same Persona prompt |
| Session restore | After submission, persist `agent_id`, `persona_preset_id`, `persona_preset_name`, `persona_snapshot`, `project_id`, and normal agent options through the shared Web conversation-config builder |
| Tool event | Only `tool == "reveal_file"` may add a candidate |
| Selection | Retain at most the first candidate per run |
| Ownership query | Exact `user_id + session_id + trace_id + file_key + source="reveal_file"` match |
| Long text | Persona config supplies an approximate Unicode character target; every segment also stays within the 2048 UTF-8 byte hard limit |

Channel differences belong in transport handling, not the model prompt. This keeps Web/PC and WeCom behavior synchronized and preserves system-prompt KV-cache reuse. A WeCom session must persist the same Persona restore metadata as a Web-created conversation so reopening it does not fall back to the base agent label.

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Same chat opens another `aibotid` or changes single/group type | Different session key and session id |
| User mapping missing or lookup fails | Send a visible account error and stop before session/task creation |
| Persona session metadata is absent | Treat as a restore defect: the Web UI may display only `fast`, `search`, or `team` |
| `reveal_project` or another tool returns a file-like dict | Ignore it |
| More than one `reveal_file` result | Keep the first; ignore and log later candidates |
| Reveal scope missing, index lookup fails, or ownership does not match | Fail closed before storage download/upload |
| Text exceeds the configured character target or 2048 UTF-8 bytes | Split at paragraph, line, sentence punctuation, space, then Unicode-safe hard boundary |

### 5. Good / Base / Bad Cases

- **Good**: user `u1`, session `s1`, trace `t1` reveals key `k1`; the same fields exist in `revealed_files`, so WeCom may upload it.
- **Good**: a Persona-routed WeCom run stores the shared conversation config; reopening the chat restores the Persona name and snapshot.
- **Base**: the model returns only text; no file storage or media upload path is entered.
- **Bad**: a tool returns a known S3 key from another trace; ownership lookup rejects it.
- **Bad**: adding WeCom-only text to the system prompt; equivalent Web and WeCom runs lose prompt parity and KV-cache reuse.

### 6. Tests Required

| Test | Assertion point |
|------|-----------------|
| `tests/infra/agent/wecom/test_session_identity.py` | Different bot/chat-type inputs create different v2 keys and sessions |
| `tests/infra/agent/wecom/test_collector_delivery.py` | Byte limit, exact text preservation, ordered independent segments, first-file selection |
| `tests/infra/test_revealed_file_storage.py` | Ownership query includes all five fields including `source` |
| `tests/infra/agent/wecom/test_preferred_agent_resolve.py` | Persona restore metadata is persisted after submit |
| Handler integration tests | Submit receives Persona/Dify options without channel prompt state; persisted trace is bound to collector; unmapped user is rejected visibly |

### 7. Wrong vs Correct

#### Wrong

```python
session_key = f"wecom:session:{chat_id}"
request.agent_options["_channel_context"] = {"channel": "wecom"}
collector.add_file_to_reveal(any_tool_result)
await backend.download(file_key)
```

#### Correct

```python
session_key = _wecom_session_key(aibotid, chat_type, chat_id)
await _persist_wecom_session_config(
    request=request, session_id=session_id, project_id=project_id
)
if tool_name == "reveal_file":
    collector.add_file_to_reveal(result)
if await revealed_files.owns_run_file(
    user_id=user_id, session_id=session_id, trace_id=trace_id, file_key=file_key
):
    await deliver(file_key)
```

## Scenario: Configurable WeCom segment target

### 1. Scope / Trigger

- Trigger: changing Persona WeCom settings or delivering a segmented reply through the normal stream, non-stream fallback, or six-minute timeout path.
- The UI value is an approximate readability target, while the byte limit remains the transport safety boundary.

### 2. Signatures

```python
class PersonaWeComConfigBase(BaseModel):
    segmented_reply: bool = True
    segment_target_chars: int = Field(600, ge=100, le=600)

def _split_by_utf8_byte_limit(
    text: str,
    byte_limit: int = 2048,
    char_limit: int | None = None,
) -> list[str]: ...
```

Frontend request/response types expose the same snake-case field:

```typescript
interface PersonaWeComConfig {
  segmented_reply: boolean;
  segment_target_chars: number;
}
```

Mongo collection `persona_wecom_config` stores `segment_target_chars` beside
`segmented_reply`. Existing documents without the field read as `600`.

### 3. Contracts

- UI presets are approximately `300`, `500`, and `600` Unicode characters.
- The selector is visible only while `segmented_reply` is enabled.
- Natural paragraph, line, sentence, and whitespace boundaries may make a segment shorter than the selected target.
- Every segment must satisfy both `len(segment) <= segment_target_chars` and `len(segment.encode("utf-8")) <= 2048`.
- Stream finalization sends segment 1 through `reply_stream(..., finish=True)` and remaining segments serially through proactive delivery.
- Non-stream and timeout fallback paths apply the same two-limit planner; they must not check only the byte limit.

### 4. Validation & Error Matrix

| Condition | Behavior |
|-----------|----------|
| Field omitted in an old Mongo document or API payload | Use `600` |
| API value below `100` or above `600` | Reject with Pydantic validation error / HTTP 422 |
| Selected target is `600`, but Emoji reaches 2048 bytes first | Split at the byte-safe Unicode boundary |
| Text is below 2048 bytes but exceeds the selected target | Still split |
| `segmented_reply=false` | Preserve the existing single-message behavior; target remains stored for later re-enable |

### 5. Good / Base / Bad Cases

- **Good**: 650 ASCII characters with target 300 become `300 + 300 + 50`.
- **Good**: Emoji input with target 600 splits before any segment exceeds 2048 bytes.
- **Base**: a short reply stays as one message.
- **Bad**: applying `segment_target_chars` only during stream finalization; fallback paths behave differently.
- **Bad**: converting the UI number directly to bytes; Chinese, ASCII, and Emoji would produce misleading character counts.

### 6. Tests Required

- Schema test: default 600; 99 and 601 are rejected.
- Splitter test: preserve exact text while enforcing both character and UTF-8 byte limits.
- Collector tests: configured target applies to normal stream finalization and non-stream delivery below 2048 bytes.
- Handler test: missing runtime field passes 600; configured value passes through to `WeComResponseCollector`.
- Frontend contract test: selector is conditional, offers 300/500/600, and the save payload contains `segment_target_chars`.

### 7. Wrong vs Correct

#### Wrong

```python
if len(text.encode("utf-8")) > 2048:
    chunks = split(text)
```

#### Correct

```python
chunks = _split_by_utf8_byte_limit(
    text,
    byte_limit=2048,
    char_limit=config.segment_target_chars,
)
if len(chunks) > 1:
    await send_in_order(chunks)
```
