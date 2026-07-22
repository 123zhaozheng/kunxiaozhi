# Marketplace skills in sandbox runtimes

## Scenario: agent-driven temporary skill installation

### 1. Scope / Trigger

Use this contract when an internal agent tool discovers a marketplace skill and materializes it into the currently attached sandbox. This is distinct from the web UI's persistent `SkillsStore` installation.

### 2. Signatures

```python
async def find_skills(query: str, tags: list[str] | None, runtime: ToolRuntime) -> str: ...
async def install_skill(name: str, runtime: ToolRuntime) -> str: ...
def build_internal_tools(*, include_sandbox_tools: bool | None = None) -> list[BaseTool]: ...
async def get_internal_tools_for_user(..., include_sandbox_tools: bool | None = None) -> list[BaseTool]: ...
def build_marketplace_skill_prompt_section(tools: Iterable[Any] | None) -> str: ...
```

### 3. Contracts

- `find_skills` returns JSON `{results, count}` with at most eight records containing `name`, `description`, `tags`, `author`, `file_count`, and `updated_at`.
- Multi-keyword marketplace search is an OR across words and across `skill_name`, `description`, and `tags`; visibility remains an AND boundary around that search.
- `install_skill` writes to `<real work_dir>/temp_skills/<sanitized name>` and returns `success`, `already_present`, `skill`, `path`, `file_count`, and `usage`.
- Resolve `work_dir` from the concrete backend (`CompositeBackend.default` when wrapped). Never guess `/workspace`, `/root`, or another fallback.
- Text content is UTF-8. Content matching `parse_binary_ref` must be fetched with `get_or_init_storage().download_file(storage_key)` and uploaded as bytes.
- The admin catalog uses default `build_internal_tools()`. Agent runtime must explicitly exclude sandbox tools for a non-sandbox agent.
- Prompt guidance requires an attached sandbox and both tools in the final policy-filtered tool list.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Missing user or backend | Structured failure; no write |
| Invalid skill name or relative path | Structured failure; no write |
| Skill missing | `code: not_found` |
| Inactive and not owned | `code: forbidden` |
| No files or no `SKILL.md` | Structured incomplete-skill failure |
| Binary reference | Download actual S3 bytes; never encode reference JSON |
| Any upload response fails or file stream is incomplete | Raise, clean staging, allow retry |
| Readable target `SKILL.md` already exists | Return `already_present: true` |
| Directory exists without readable `SKILL.md` | Treat as incomplete and replace only after staging succeeds |
| Tool disabled by policy | Tool absent and marketplace prompt absent |

### 5. Good / Base / Bad Cases

- Good: all files upload to a UUID staging directory, expected paths match, and a single sandbox command moves staging into the final directory.
- Base: the final `SKILL.md` already exists, so installation returns idempotently without downloading files again.
- Bad: checking only whether a target directory is non-empty. A prior partial upload can then masquerade as a complete installation.
- Bad: `content.encode()` on a binary-reference JSON string. This produces a JSON text file with a `.png`, `.pdf`, or other binary extension.

### 6. Tests Required

- Assert the path starts with the fake concrete backend's `work_dir`, not `/skills/` or a hard-coded fallback.
- Assert a binary-ref fixture downloads by `storage_key` and uploads byte-for-byte.
- Fail one upload, assert staging cleanup, retry, and assert success.
- Assert an incomplete target is not `already_present`; a readable `SKILL.md` is.
- Assert FastAgent excludes tools while the admin catalog and sandbox agents can include them.
- Disable either policy entry and assert both runtime absence and prompt absence.
- Assert multi-word search query shape preserves visibility and ANY-word matching.

### 7. Wrong vs Correct

#### Wrong

```python
target = "/workspace/temp_skills/name"
if await backend.als(target):
    return {"already_present": True}
await backend.aupload_files([(target + "/asset.png", binary_ref_json.encode())])
```

#### Correct

```python
work_dir = await resolve_real_backend_work_dir(backend)
target = f"{work_dir}/temp_skills/{safe_name}"
if await readable_skill_md(backend, target):
    return {"already_present": True}
payload = await storage.download_file(binary_ref.storage_key)
# upload to unique staging, validate every response, then atomically move to target
```
