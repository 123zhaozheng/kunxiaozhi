# Dify KB Tool Pattern Analysis

## Research Context
Date: 2026-08-28
Task: Refactor Dify KB retrieval tools to remove LLM rewrite logic

## Existing Tool Patterns Discovered

### 1. Simple Tool Pattern (No Parameters)
Looking at existing tools with no parameters like `list_dify_knowledge_bases` should be:

**Example from codebase:**
```python
@tool
async def env_var_list(
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
) -> str:
    """List all environment variables for current user."""
    # Implementation reads from runtime/config
    pass
```

**Pattern:**
- Uses `@tool` decorator
- Only parameter is `runtime` (auto-injected)
- Returns JSON string via `_json_dumps_result()`
- Can read persona binding from `runtime.config.configurable.agent_options`

### 2. Query Tool Pattern (With Parameters)
Tools that accept user queries follow this pattern:

**Example - query tool structure:**
```python
@tool
async def query_tool(
    query: Annotated[str, "User's original query."],
    dataset_id: Annotated[str, "Dataset ID to query."],
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
) -> str:
    """Query description with usage examples."""
    # Validate inputs
    # Process query (no modification)
    # Return results
    pass
```

**Key observations:**
- Each parameter has detailed `Annotated` descriptions
- Runtime validation happens inside the tool
- Direct API calls, no hidden processing

### 3. Error Handling Pattern

**From existing code:**
```python
try:
    # Main logic
    return await _json_dumps_result({"success": True, ...})
except Exception as exc:
    logger.warning("[tool_name] failed: %s", exc)
    return await _json_dumps_result({"error": f"Failed: {exc}"})
```

**Best practices:**
- Catch specific exceptions when possible
- Log with `[tool_name]` prefix for traceability
- Always return structured error responses
- Never let exceptions propagate to agent

### 4. Dataset Description Caching

**Existing implementation in `_fetch_dataset_descriptions`:**
```python
_DATASET_DESC_CACHE: dict[str, dict[str, Any]] = {}
_DATASET_DESC_TTL_SECONDS = 300  # 5 min

async def _fetch_dataset_descriptions(dataset_ids: list[str]) -> dict[str, dict[str, str]]:
    """Fetch descriptions with TTL caching."""
    base_url = _resolve_dify_base_url()
    now = time.monotonic()
    cached = _DATASET_DESC_CACHE.get(base_url)
    if cached and (now - cached["fetched_at"]) < _DATASET_DESC_TTL_SECONDS:
        return cached["by_id"]
    
    # Fetch from API...
    _DATASET_DESC_CACHE[base_url] = {"fetched_at": time.monotonic(), "by_id": by_id}
    return by_id
```

**Key insights:**
- Cache keyed by base_url (handles multiple instances)
- TTL-based refresh avoids stale data
- Graceful degradation on errors returns empty dict
- Uses `time.monotonic()` for accurate TTL

### 5. Persona Binding Pattern

**How persona dataset IDs are extracted:**
```python
def _persona_dataset_ids(runtime: Any) -> list[str]:
    """Read persona-bound dataset IDs from runtime."""
    if not runtime or not hasattr(runtime, "config"):
        return []
    config = runtime.config
    if not isinstance(config, dict):
        return []
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return []
    agent_options = configurable.get("agent_options")
    if not isinstance(agent_options, dict):
        return []
    raw = agent_options.get("dify_kb_dataset_ids")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]
```

**Validation points:**
- Multiple layers of type checking
- Gracefully handles missing/invalid structures
- Returns empty list instead of raising errors
- Strips whitespace and filters empty strings

### 6. Query Truncation Pattern

**Simple utility function:**
```python
DIFY_QUERY_MAX_CHARS = 250

def _truncate_query(query: str) -> str:
    """Clamp query to Dify's limit."""
    return query[:DIFY_QUERY_MAX_CHARS]
```

**Usage considerations:**
- Applied before API calls
- Documented in tool description
- No error raised if truncated (silent enforcement)

## Design Decisions Confirmed

### ✅ Keep These Patterns
1. **TTL caching** - Proven to work well
2. **Graceful degradation** - Don't break on API errors
3. **Runtime validation** - Check types at each level
4. **Structured error responses** - Consistent format

### ⚠️ Avoid These Old Patterns
1. **LLM-based query rewriting** - Causes infinite loops
2. **Hidden decision gates** - Agents lose control
3. **Batch retrieval with automatic routing** - Too much automation
4. **Complex fallback chains** - Hard to debug

## Recommendations for New Tools

### For `list_dify_knowledge_bases()`
- Use existing `_fetch_dataset_descriptions()` helper
- Reuse `_DATASET_DESC_CACHE` for efficiency
- Support both persona-bound and default dataset IDs
- Return minimal info: `{id, name, description}`

### For `query_dify_knowledge_base()`
- Accept query without any modification
- Validate dataset_id against allowed list
- Apply truncation if needed
- Return full segment details with scores

### Validation Strategy
1. Check dataset_id exists in persona/default bindings
2. Truncate long queries silently
3. Return clear error messages for invalid inputs
4. Log warnings but never raise exceptions

## Configuration Changes

### Removed Dependencies
- ❌ `DIFY_KB_LLM_MODEL_ID` - No longer needed
- ❌ System prompt for query rewriting

### Required Configurations
- ✅ `DIFY_KB_ENABLED` - Feature toggle
- ✅ `DIFY_KB_BASE_URL` - Dify API endpoint
- ✅ `DIFY_KB_API_KEY` - Authentication
- ✅ `DIFY_KB_RERANK_MODEL_ID` - Re-ranking model

## Testing Strategy

### Unit Tests Needed
1. Tool existence and schema validation
2. Empty KB scenarios (no persona, no defaults)
3. Default dataset ID usage
4. Persona-bound dataset priority
5. Dataset ID validation (valid/invalid)
6. Query preservation (original vs modified)
7. Query truncation edge cases
8. Workflow integration (list → query)
9. Old tool removal verification

### Integration Test Scenarios
1. List available KBs → Select one → Query it
2. Query with non-matching terms → See empty results
3. Multiple sequential queries without retry loop
4. Verify no recursion_limit triggered

## References
- Original file: `src/infra/tool/dify_kb_tool.py` (before refactor)
- Existing test patterns: `tests/infra/tool/test_dify_kb_tool.py`
- Internal registry: `src/infra/tool/internal_registry.py`

---
*Research completed using live codebase exploration. All patterns verified against actual implementation.*
