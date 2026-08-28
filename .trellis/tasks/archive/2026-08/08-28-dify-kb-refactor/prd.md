# Refactor Dify KB retrieval tools - remove LLM rewrite logic

## Problem Statement

Current `dify_kb_retrieve` tool has a critical design flaw: it uses LLM to rewrite query statements internally (`_llm_decide_retrieval`), which causes agents to lose control over the retrieval process. When no knowledge is found, agents only receive empty results (records: []), unable to distinguish between "tried everything but no results" vs "should try a different approach", leading to infinite loops and ultimately triggering recursion_limit (default 100) and disconnecting the conversation.

This violates the minimum code principle and easily disrupts agent retrieval intent.

## Goal

Completely refactor the knowledge base retrieval toolset by removing all internal LLM rewriting logic and providing two simple, transparent independent tools that give agents full control over the retrieval process.

## Requirements

### Core Requirements

1. **Remove all internal LLM rewrite logic**
   - Delete `_llm_decide_retrieval` function and related system prompt
   - Remove LLM-based decision gate for whether retrieval is needed
   
2. **Provide two standalone tools:**
   
   **Tool 1: `list_dify_knowledge_bases()`**
   - Return format: `[{id, name, description}]`
   - Scope: Only return knowledge bases bound to current Persona
   - Fallback: If no Persona binding, use system default `DIFY_KB_DEFAULT_DATASET_IDS`
   - No parameters required
   
   **Tool 2: `query_dify_knowledge_base(query: str, dataset_id: str)`**
   - Query parameter: Agent's original query string (NO rewriting applied)
   - Dataset parameter: Specify which knowledge base ID to query
   - Return format: Same as existing format with segments, score, document info, etc.

3. **Preserve core functionality:**
   - Keep core retrieval logic in `src/infra/tool/dify_kb_tool.py`
   - Maintain Dify API calls
   - Preserve parallel retrieval implementation
   - Keep deduplication logic
   - Preserve reranking functionality

4. **Update documentation:**
   - Update tool docstrings to clearly instruct Agents on usage (list first, then query)
   - Remove old version `dify_kb_retrieve` tool completely

### Constraints

- Must not break existing agent workflows that use Dify KB retrieval
- Must maintain compatibility with existing configuration settings
- Must preserve error handling and logging patterns
- All changes must be testable with existing test infrastructure

## Acceptance Criteria

- [x] `_llm_decide_retrieval` function removed from codebase
- [x] `_LLM_SYSTEM_PROMPT` constant removed from codebase
- [x] New `list_dify_knowledge_bases()` tool implemented and working
- [x] New `query_dify_knowledge_base()` tool implemented and working
- [x] Old `dify_kb_retrieve` tool removed from `internal_registry.py`
- [x] Tool descriptions updated in `harness_prompt_overrides.py`
- [x] Tools properly documented with usage examples
- [x] Unit tests written for both new tools (18 tests passing)
- [x] Tests pass successfully
- [x] Agent testing validates no more infinite loop behavior

## Technical Notes

### Files Modified

1. ✓ `src/infra/tool/dify_kb_tool.py` - Completely rewritten with two new tools
2. ✓ `src/infra/tool/internal_registry.py` - Updated to register new tools
3. ✓ `src/agents/core/harness_prompt_overrides.py` - Updated tool descriptions  
4. ✓ `tests/infra/tool/test_dify_kb_new_tools.py` - NEW test file created
5. ✗ `tests/infra/tool/test_dify_kb_tool.py` - DELETED (referenced removed functions)

### Key Implementation Points

1. `list_dify_knowledge_bases()`:
   - ✓ Fetch dataset descriptions from Dify API
   - ✓ Filter by persona-bound dataset IDs or use default
   - ✓ Cache with TTL (reuse existing `_DATASET_DESC_CACHE`)
   - ✓ Return simplified list format: `[{id, name, description}]`

2. `query_dify_knowledge_base()`:
   - ✓ Accept raw query without modification (no LLM rewrite)
   - ✓ Validate dataset_id against persona/default bindings
   - ✓ Call single dataset retrieval endpoint
   - ✓ Apply same deduplication and reranking as before
   - ✓ Return full segment details

3. Deprecation:
   - ✓ Old `dify_kb_retrieve` tool completely removed
   - ✓ All references removed (including old test file)
   - ✓ `DIFY_KB_LLM_MODEL_ID` configuration no longer required

## Notes

This task requires careful attention to the agent workflow impact. The goal is to simplify the interface while maintaining powerful underlying capabilities.
