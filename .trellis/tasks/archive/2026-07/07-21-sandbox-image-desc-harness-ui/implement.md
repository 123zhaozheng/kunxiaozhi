# Implement: 沙箱镜像能力描述注入 + Harness UI 归类

## Preflight

- Active task: `.trellis/tasks/07-21-sandbox-image-desc-harness-ui`
- Read first: `prd.md`, `design.md`, `research/*.md`
- Spec (on demand): `.trellis/spec/backend/sandbox-providers.md`, `.trellis/spec/backend/agent-harness.md`（若存在）
- Do **not** `task.py start` until user reviews this plan.

## Ordered checklist

### 1. Settings: new field + harness category move

- [ ] `src/kernel/config/base.py`: add `SANDBOX_IMAGE_DESCRIPTION: str = ""`
- [ ] `src/kernel/config/definitions.py`:
  - [ ] Add `SANDBOX_IMAGE_DESCRIPTION` (`TEXT`, `SANDBOX`, `general`, `depends_on=ENABLE_SANDBOX`, `default=""`, `frontend_visible=True`)
  - [ ] Move `AGENT_HARNESS_MODE` → `category=SettingCategory.AGENT`, `subcategory="harness"` (or `"prompt"`)
- [ ] Confirm **not** in `RESTART_REQUIRED_SETTINGS` / `_SANDBOX_AFFECTED_SETTINGS` for the new key
- [ ] i18n `settingDesc.SANDBOX_IMAGE_DESCRIPTION` in `zh.json` + `en.json`；必要时微调 `AGENT_HARNESS_MODE` 描述（仍写清需重启）

**Validate:** grep definitions + base field present; Settings UI（admin）在 sandbox + agent 分类可见。

### 2. Capability section builder

- [ ] Add `src/infra/sandbox/capability_prompt.py` with `build_sandbox_capability_section(description: str | None) -> str`
  - empty/whitespace → `""`
  - non-empty → Chinese heading + boundary framing + admin body (per design.md)
- [ ] Unit tests: empty / whitespace / content shape

**Validate:** `uv run pytest tests/...` for new tests green.

### 3. Wire search_agent

- [ ] `search_agent/nodes.py` main `_prompt_sections`: if `sandbox_backend`, append capability **before** runtime section
- [ ] Subagent `subagent_prompt_sections`: same order
- [ ] Import builder + `settings.SANDBOX_IMAGE_DESCRIPTION` (or pass from caller)

**Validate:** targeted test or manual assert section order.

### 4. Wire team_agent

- [ ] `team_agent/nodes.py` main + role/subagent paths：与 search 对称
- [ ] Import shared builder only（不要复制 team_agent/prompt 分叉文案）

**Validate:** team path with sandbox mock includes section.

### 5. Tests & regression

- [ ] New unit tests for builder
- [ ] Light wiring tests if cheap (middleware sections list contains capability when settings set)
- [ ] Run related existing: sandbox prompt / subagent prompts / settings definition smoke if any

**Validate:**

```bash
uv run pytest tests/ -q -k "capability or sandbox_mcp_prompt or harness or subagent_prompts" --tb=short
```

（按实际新增测试文件名调整 filter）

### 6. Manual smoke (optional before finish)

- [ ] 设置里写一段描述 → 开 search 沙箱会话 → 日志/调试看 system 含「沙箱环境能力边界」
- [ ] 清空描述 → 不再出现
- [ ] Settings → agent 分类可见 harness 下拉；LLM/cache 不再出现该项

## Review gates

| Gate | Criteria |
|------|----------|
| G1 after step 1 | 配置定义与 i18n 落地，无误把 description 标成 restart |
| G2 after step 3–4 | 仅 sandbox_backend 路径注入；fast 无变更 |
| G3 after step 5 | 相关测试绿；空描述零回归 |

## Rollback points

1. After settings only: remove key + revert category（DB 可能残留 key，无害）。
2. After builder only: unused module delete。
3. After wiring: revert nodes imports/appends。

## Out of order rules

- Do not implement harness hot-reload.
- Do not add per-platform description keys.
- Do not put description into `SANDBOX_SYSTEM_PROMPT` string.

## Manifests (sub-agent)

- Update `implement.jsonl` / `check.jsonl` with paths from design Key files before `task.py start`.
