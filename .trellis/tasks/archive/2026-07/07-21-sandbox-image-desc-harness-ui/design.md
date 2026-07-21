# Design: 沙箱镜像能力描述注入 + Harness UI 归类

## Goals

1. 管理员可在系统设置（沙箱类）配置全局「沙箱环境能力描述」，启用沙箱的 search/team agent 将其注入 system prompt。
2. 将 `AGENT_HARNESS_MODE` 从 LLM/cache 归到 AGENT 设置分类，便于在「代理」相关设置区发现；默认 `compact_zh` 不变，重启语义不变。

## Non-goals

- 不做 per-platform / per-user 描述字段。
- 不改 harness catalog 文案、不实现 harness 热更新。
- 不在 AgentModelPanel 单独做卡片（仅靠 Settings 分类迁移）。
- 不给 fast_agent 注入（无沙箱路径）。
- 不把描述拼进 base `SANDBOX_SYSTEM_PROMPT`、不进 `_SANDBOX_AFFECTED_SETTINGS`。

## Architecture

```
Admin UI (Settings → sandbox)
  └─ SANDBOX_IMAGE_DESCRIPTION (SettingType.TEXT, depends ENABLE_SANDBOX)
       ↓ persist via settings API
settings.SANDBOX_IMAGE_DESCRIPTION
       ↓ each agent graph build (search/team when sandbox_backend)
build_sandbox_capability_section(text) → "" | "## …\n{text}"
       ↓ SectionPromptMiddleware sections
  [capability section] → [SANDBOX_RUNTIME_SECTION work_dir] → MCP → env
```

Harness UI:

```
definitions.AGENT_HARNESS_MODE.category: LLM/cache  →  AGENT (subcategory e.g. harness|prompt)
SettingsPanel 已有 agent 分类与 SELECT 渲染；选项与 API 不变
```

## Data / settings contract

### New setting

| Field | Value |
|-------|--------|
| Key | `SANDBOX_IMAGE_DESCRIPTION` |
| Type | `SettingType.TEXT` |
| Category | `SettingCategory.SANDBOX` |
| Subcategory | `general`（与 `ENABLE_SANDBOX` 同组，平台无关） |
| Default | `""` |
| depends_on | `"ENABLE_SANDBOX"` |
| frontend_visible | `True`（运维可配） |
| is_sensitive | False |
| Restart | **否**（勿加入 `RESTART_REQUIRED_SETTINGS`） |
| Sandbox manager rebuild | **否**（勿加入 `_SANDBOX_AFFECTED_SETTINGS`） |

Also add field on `Settings` in `base.py` with default `""`.

### Existing setting move

| Field | Change |
|-------|--------|
| `AGENT_HARNESS_MODE` | `category`: `LLM` → `AGENT` |
| subcategory | `cache` → `harness`（或 `prompt`；与现有 agent 子类一致即可） |
| options / default / restart | 不变 |

i18n: 补 `settingDesc.SANDBOX_IMAGE_DESCRIPTION`（en/zh 至少）；`AGENT_HARNESS_MODE` 描述已含重启提示，可小幅改写「位于代理设置」。

## Prompt injection design

### Builder

Shared helper（推荐新文件或挂在 search prompt 旁，team 只 import 一次）：

```python
# e.g. src/infra/sandbox/capability_prompt.py  或  search_agent/prompt.py

def build_sandbox_capability_section(description: str | None) -> str:
    # Admin owns full wording; builder only strips and empty-checks.
    return (description or "").strip()
```

- **不内置**标题/边界说明外壳；管理员在设置里写完整文案（可自带 `##` 标题）。
- 空/纯空白 → `""`，不注入。

### Wire-up points

| Agent | File | Where |
|-------|------|--------|
| search main | `search_agent/nodes.py` | 构建 `_prompt_sections` 时：`if sandbox_backend:` 追加 capability（在 `SANDBOX_RUNTIME_SECTION` **之前**） |
| search sub | same | `subagent_prompt_sections` 同样：capability → runtime |
| team main | `team_agent/nodes.py` | 与 search 对称；import builder + 读 `settings.SANDBOX_IMAGE_DESCRIPTION` |
| team sub | same | 与 runtime section 同路径 |
| fast | — | 不改 |

读取：`from src.kernel.config import settings` → `settings.SANDBOX_IMAGE_DESCRIPTION`（每轮构图时读，改设置后下一轮生效）。

Gate：`if sandbox_backend`（与现网 sandbox 注入一致）；capability 仅在 `build_...` 非空时 append。

### Order (main, sandbox on)

```
MAIN_AGENT / persona / skills / memory_guide
→ [NEW] sandbox capability section   # semi-stable, admin text
→ SANDBOX_RUNTIME_SECTION (work_dir) # session-specific
→ goal (optional)
→ SandboxMCPMiddleware
→ EnvVarPromptMiddleware
→ …
```

## Compatibility & rollout

- 默认 `""`：零行为变化。
- 已有会话：下一轮 agent 重建后可见新描述；**不**杀已有沙箱容器。
- Harness category 移动：仅 UI 分组变化；旧书签/文档若写「LLM cache」需知会。
- Rollback：清空描述 + 还原 category 定义即可。

## Tradeoffs

| Decision | Why |
|----------|-----|
| 全局一份 vs 按平台 | 用户已选全局；换平台时管理员可手改同一文案 |
| Section vs middleware | 静态 settings 字符串，无需 async/user cache |
| 不进 base system_prompt | 保持 harness 基座稳定 + 空值易跳过 + 更好的 KV 分段 |
| Harness 仅改 category | 最低成本满足「放到代理」；不做热更（代价高） |

## Test strategy

- Unit: `build_sandbox_capability_section` 空/空白/正文。
- Unit/integration: search/team nodes 在 mock sandbox 时 sections 含描述；无 sandbox 时不含。
- Settings: definition 存在、type=text、depends_on ENABLE_SANDBOX；`AGENT_HARNESS_MODE` category=agent。
- 回归：现有 `test_subagent_prompts` / sandbox prompt 测试不破。

## Key files to touch

- `src/kernel/config/base.py` — 新字段
- `src/kernel/config/definitions.py` — 新定义 + harness category
- `src/infra/sandbox/capability_prompt.py`（新）或 `search_agent/prompt.py`
- `src/agents/search_agent/nodes.py`
- `src/agents/team_agent/nodes.py`
- `frontend/src/i18n/locales/en.json`, `zh.json`
- `tests/...` 对应新增

## Risks

1. **描述过长**：无 max_length；依赖管理员自律。可后续加限制。
2. **注入 prompt injection**：管理员可控文本，与现有 TEXT 设置同信任级；外壳已说明为能力边界 data。
3. **Team 重复定义**：继续以 search/shared helper 为唯一源，避免 team 本地 fork。
