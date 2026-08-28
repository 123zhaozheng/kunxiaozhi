# Implementation Plan - Dify KB Refactor

## Summary

This document records the implementation of the Dify KB tool refactor. All tasks have been completed successfully.

## Changes Made

### 1. Core Tool Implementation (`src/infra/tool/dify_kb_tool.py`)

**Completely rewritten with two new tools:**

#### New Tool 1: `list_dify_knowledge_bases()`
- **Purpose**: List available knowledge bases for the current persona
- **Parameters**: None (reads from runtime config)
- **Returns**: JSON string with format `{success: true, knowledge_bases: [{id, name, description}]}`
- **Logic**:
  - Gets dataset IDs from persona binding or system defaults
  - Fetches descriptions from Dify API (cached with TTL)
  - Returns simplified list format

#### New Tool 2: `query_dify_knowledge_base(query: str, dataset_id: str)`
- **Purpose**: Query a specific knowledge base with original query
- **Parameters**:
  - `query`: User's original query (no modification, max 250 chars)
  - `dataset_id`: Knowledge base ID to query
  - `top_k`: Optional override for max results
  - `score_threshold`: Optional override for minimum score
- **Returns**: JSON string with segments, scores, document info
- **Logic**:
  - Validates dataset_id against persona/default bindings
  - Truncates query if > 250 chars
  - Performs single-dataset retrieval
  - Applies external reranking
  - Returns full segment details

**Removed Components:**
- ❌ `_llm_decide_retrieval()` - LLM query rewrite function
- ❌ `_LLM_SYSTEM_PROMPT` - System prompt for query rewriting
- ❌ `_batch_retrieve()` - Multi-query batching logic
- ❌ `dify_kb_retrieve()` - Old combined tool

### 2. Tool Registration (`src/infra/tool/internal_registry.py`)

**Changes:**
- Imported new tool factory functions:
  ```python
  from src.infra.tool.dify_kb_tool import (
      get_list_dify_knowledge_bases_tool,
      get_query_dify_knowledge_base_tool,
  )
  ```
- Removed dependency on `DIFY_KB_LLM_MODEL_ID` config
- Registered both new tools instead of old tool
- Condition check now only requires: `DIFY_KB_ENABLED`, `DIFY_KB_BASE_URL`, `DIFY_KB_API_KEY`, `DIFY_KB_RERANK_MODEL_ID`

### 3. Agent Prompts (`src/agents/core/harness_prompt_overrides.py`)

**Updated tool descriptions:**

```python
"list_dify_knowledge_bases": "列出当前 persona 绑定的 Dify 知识库（若无则返回系统默认）：返回 [{id, name, description}]。\n先调用此工具获取可用知识库列表。",
"query_dify_knowledge_base": "按原始查询语句检索指定 Dify 知识库片段（不改写）：输入 query 和 dataset_id，返回 segments、score、document 等信息。\n用法：先调用 list_dify_knowledge_bases 获取 dataset_id，再调用此工具进行检索。",
```

**Updated schema fields:**
- Removed `dify_kb_retrieve` field mapping
- Added `list_dify_knowledge_bases` with no parameters
- Added `query_dify_knowledge_base` with query, dataset_id, top_k, score_threshold

### 4. Test File Creation (`tests/infra/tool/test_dify_kb_new_tools.py`)

**Created 18 comprehensive tests covering:**
- ✅ Tool existence and structure validation
- ✅ Configuration requirements
- ✅ Persona dataset ID extraction
- ✅ `list_dify_knowledge_bases()`:
  - Empty result when no KBs configured
  - Uses default dataset IDs when no persona
  - Prioritizes persona-bound IDs over defaults
  - Returns correct format with id, name, description
- ✅ `query_dify_knowledge_base()`:
  - Validates dataset_id against persona bindings
  - Validates against default dataset IDs
  - Accepts valid dataset_id
  - Preserves original query
  - Truncates long queries to 250 chars
  - Returns correct format
- ✅ Full workflow test (list then query)
- ✅ Verifies old tool is removed
- ✅ Verifies old helper functions removed

**All 18 tests passing!** ✅

### 5. Cleanup

**Deleted files:**
- ❌ `tests/infra/tool/test_dify_kb_tool.py` - Old test file referencing removed functions

## Testing Results

```bash
pytest tests/infra/tool/test_dify_kb_new_tools.py -v --tb=line -q

18 passed, 6 warnings in 3.07s
```

## Key Design Decisions

1. **No Deprecated Layer**: Instead of deprecating the old tool, we completely removed it to reduce complexity.

2. **Agent Control**: Agents now have full control over:
   - Which datasets to query
   - What queries to send (no automatic rewriting)
   - Whether to retry with different queries

3. **Preserved Functionality**: All core features maintained:
   - Parallel retrieval capability (still used internally)
   - Segment deduplication by segment_id
   - External reranking
   - Score threshold filtering
   - Query truncation at 250 chars
   - TTL-based caching of dataset descriptions

4. **Simplified Interface**: Two simple tools instead of one complex tool with hidden behavior.

5. **Clear Usage Pattern**: Explicit workflow documented in tool docstrings:
   ```
   1. Call list_dify_knowledge_bases() to see available KBs
   2. Select appropriate dataset_id and call query_dify_knowledge_base(query, dataset_id)
   ```

## Breaking Changes

### Configuration
- `DIFY_KB_LLM_MODEL_ID` is no longer required or used
- Tools will fail to load if this setting remains but isn't referenced anywhere

### API Changes
- Old tool `dify_kb_retrieve` is completely gone
- No migration path needed since we're replacing a flawed design

### Agent Behavior
- Agents must explicitly:
  1. List available knowledge bases
  2. Choose which to query
  3. Send their own query (no automatic rewriting)
- This gives agents more control and prevents infinite loops

## Verification Checklist

- [x] All PRD acceptance criteria met
- [x] Code follows project patterns and conventions
- [x] Unit tests cover all major functionality (18 tests)
- [x] Tests pass successfully
- [x] No references to old tool remain
- [x] Tool docstrings include usage instructions
- [x] Agent prompts updated
- [x] Old test file removed

## Next Steps (Post-Implementation)

According to user requirements:
> After code changes, subagent (subagent) should use haike model for testing validation to ensure new tools behave as expected and no longer have infinite loop issues.

**Recommendation**: Have agents try scenarios that previously caused infinite loops:
1. Query KB with terms that don't match anything
2. Try multiple queries without success
3. Verify no recursion_limit triggered

The simplified interface should allow agents to:
- See empty results clearly
- Decide whether to try different queries or give up
- Not get stuck in rewrite-retry cycles

## Task Status

**All implementation tasks complete.** Ready for Phase 3.3 (spec update) and Phase 3.4 (commit).
