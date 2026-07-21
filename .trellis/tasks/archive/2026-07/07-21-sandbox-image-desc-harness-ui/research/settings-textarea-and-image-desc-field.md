# Research: settings textarea & sandbox image/template capability description

- **Query**: 为「沙箱镜像能力描述」新配置项找可复用的设置定义 / 前端控件 / 持久化模式（尤其多行文本 textarea）；并覆盖 SettingType、SANDBOX 依赖模式、热更新、i18n、E2B/Daytona 对称性。
- **Scope**: internal
- **Date**: 2026-07-21

## Summary

- 已存在 `SettingType.TEXT`（`"text"`），前端 `SettingsPanel` 对 `type === "text"` 渲染多行 `<textarea rows={8}>`，**无需新增 SettingType 或前端控件类型**。
- 当前全库仅 **1** 处使用 `SettingType.TEXT`：`SESSION_TITLE_PROMPT`（会话标题 prompt 模板），是设置系统内「长文本描述类」的直接样板。
- SANDBOX 类配置已有完整的 `depends_on` + `subcategory` 分平台模式（`daytona` / `e2b` / `opensandbox`），镜像/模板键为 `DAYTONA_IMAGE` / `E2B_TEMPLATE` / `OPENSANDBOX_IMAGE`，**尚无任何「能力描述」字段**。
- 沙箱相关键已纳入热更新：`refresh_settings` 后会 soft-reset `SessionSandboxManager` 单例，且 **不** 在 `RESTART_REQUIRED_SETTINGS` 中；`settings.XXX` 即时 `setattr` 生效。
- 已创建的 sandbox session：改镜像/模板/连接参数 **不** 改写正在运行的沙箱；改「描述注入 prompt」若每次 agent 构建时读 `settings`，可对**下一次**消息生效，与「重建沙箱」无关。
- E2B/Daytona **可对称**增加 `*_DESCRIPTION` 键，模式与 `OPENSANDBOX_IMAGE` 并列一致；是否做对称由产品确认（PRD open Q2）。

## Setting type & UI control map

### Backend enum (`src/kernel/schemas/setting.py`)

| SettingType | value | 含义 |
|---|---|---|
| `STRING` | `"string"` | 短字符串 |
| `TEXT` | `"text"` | 长文本（注释：renders as textarea） |
| `NUMBER` | `"number"` | 数字 |
| `BOOLEAN` | `"boolean"` | 布尔 |
| `JSON` | `"json"` | JSON 对象/数组 |
| `SELECT` | `"select"` | 下拉（`options` 字段） |

**不存在** `TEXTAREA` 类型名；多行文本类型名就是 `TEXT`。

### Frontend type union (`frontend/src/types/settings.ts`)

与后端对齐：`"string" | "text" | "number" | "boolean" | "json" | "select"`。

### SettingsPanel 控件映射 (`frontend/src/components/panels/SettingsPanel.tsx`)

| type | 控件 | 备注 |
|---|---|---|
| `"text"` | `<textarea rows={8}>` | 专用分支；普通文本样式 |
| `"json"` + `json_schema` | `JsonSchemaEditor` | 结构化编辑 |
| `"json"` 无 schema | `<textarea rows={20} font-mono>` | 原始 JSON |
| `"boolean"` / `"select"` / 若干 model 键 | `GlassSelect` | boolean 为 true/false 选项 |
| 其它（含 `"string"` / `"number"`） | `<input type="text|number">` | 单行 |

`TYPE_COLORS`（`SettingsPanel.constants.ts`）已含 `text`（cyan 标签色）。

### 持久化类型校验 (`src/infra/settings/storage.py` `set`)

- `string` / `text`：均 `value = str(value)`，**无 max_length / 行数限制**。
- 无字段级 maxLength 配置；定义 dict 也不支持 max_length 键（当前 schema 仅有 type/category/subcategory/description/default/depends_on/options/json_schema/is_sensitive/frontend_visible 等）。

### `depends_on` / `frontend_visible` 语义

- `depends_on: "ENABLE_SANDBOX"`：父项为 true 时显示。
- `depends_on: {"key": "SANDBOX_PLATFORM", "value": "opensandbox"}`：平台匹配时显示。
- 前端 `isSettingVisible`（SettingsPanel ~L211）实现上述两种形态。
- `frontend_visible`：非 admin 模式只返回 `frontend_visible=True` 的项；**管理端 Settings 面板**以 admin 模式拉全量，未标 `frontend_visible` 的键仍对管理员可见。
- OpenSandbox 部分键显式 `frontend_visible: True`（DOMAIN/IMAGE/WORK_DIR/USE_SERVER_PROXY）；E2B/Daytona 多数未标（默认 False），仅 admin 可见。

## SANDBOX 类别现有模式

### 定义位置

`src/kernel/config/definitions.py` L370–555（Sandbox Settings 块）。

### 全量 SANDBOX 键（摘要）

| Key | type | subcategory | depends_on | frontend_visible |
|---|---|---|---|---|
| `ENABLE_SANDBOX` | BOOLEAN | general | — | True |
| `SANDBOX_PLATFORM` | SELECT | general | `ENABLE_SANDBOX` | — |
| `SANDBOX_GREP_TIMEOUT` | NUMBER | general | `ENABLE_SANDBOX` | — |
| `SANDBOX_MCP_REBUILD_CONCURRENCY` | NUMBER | general | `ENABLE_SANDBOX` | — |
| `DAYTONA_API_KEY` | STRING (sensitive) | daytona | platform=daytona | — |
| `DAYTONA_SERVER_URL` | STRING | daytona | platform=daytona | — |
| `DAYTONA_TIMEOUT` | NUMBER | daytona | platform=daytona | — |
| `DAYTONA_IMAGE` | STRING | daytona | platform=daytona | — |
| `DAYTONA_AUTO_*` | NUMBER | daytona | platform=daytona | — |
| `E2B_API_KEY` | STRING (sensitive) | e2b | platform=e2b | — |
| `E2B_TEMPLATE` | STRING (default `"base"`) | e2b | platform=e2b | — |
| `E2B_TIMEOUT` | NUMBER | e2b | platform=e2b | — |
| `E2B_AUTO_PAUSE` / `E2B_AUTO_RESUME` | BOOLEAN | e2b | platform=e2b | — |
| `OPENSANDBOX_DOMAIN` | STRING | opensandbox | platform=opensandbox | True |
| `OPENSANDBOX_API_KEY` | STRING (sensitive) | opensandbox | platform=opensandbox | — |
| `OPENSANDBOX_IMAGE` | STRING (default `"ubuntu"`) | opensandbox | platform=opensandbox | True |
| `OPENSANDBOX_TIMEOUT` | NUMBER | opensandbox | platform=opensandbox | — |
| `OPENSANDBOX_WORK_DIR` | STRING | opensandbox | platform=opensandbox | True |
| `OPENSANDBOX_USE_SERVER_PROXY` | BOOLEAN | opensandbox | platform=opensandbox | True |

`SANDBOX_PLATFORM.options`: `["daytona", "e2b", "opensandbox"]`，default `"daytona"`。

### 运行时字段（`src/kernel/config/base.py` Settings）

对应镜像/模板字段：

- `DAYTONA_IMAGE: str = ""`
- `E2B_TEMPLATE: str = "base"`
- `OPENSANDBOX_IMAGE: str = "ubuntu"`

**无** `*_DESCRIPTION` / `*_IMAGE_DESC` 字段。

### 镜像/模板消费点

- `SessionSandboxManager.__init__` 按 platform 构造 adapter，读入 image/template（`session_manager.py` ~L359–376）。
- `OpenSandboxSandboxAdapter._sync_from_settings` 在 `create_sandbox` 前再同步一次 image 等（~L230–239）。
- Daytona 创建时 `snapshot=settings.DAYTONA_IMAGE if settings.DAYTONA_IMAGE else None`（~L799）。
- E2B adapter 使用构造时的 template；manager soft-reset 后新实例会读新值。

## Recommended new setting keys (OpenSandbox / E2B / Daytona)

> 以下为与现网模式对齐的**可复用命名建议**（调研结论；实现时由 design 定稿）。

### 推荐 A：按平台对称（与 IMAGE/TEMPLATE 绑定）

| Key | type | category | subcategory | depends_on | default | notes |
|---|---|---|---|---|---|---|
| `OPENSANDBOX_IMAGE_DESCRIPTION` | `SettingType.TEXT` | SANDBOX | `opensandbox` | `{"key":"SANDBOX_PLATFORM","value":"opensandbox"}` | `""` | 主需求；可 `frontend_visible: True` 与 IMAGE 一致 |
| `E2B_TEMPLATE_DESCRIPTION` | `SettingType.TEXT` | SANDBOX | `e2b` | platform=`e2b` | `""` | 对称；对应 `E2B_TEMPLATE` |
| `DAYTONA_IMAGE_DESCRIPTION` | `SettingType.TEXT` | SANDBOX | `daytona` | platform=`daytona` | `""` | 对称；对应 `DAYTONA_IMAGE` |

配套改动面（实现时，不在本 research 范围执行）：

1. `SETTING_DEFINITIONS` 增加上述键。
2. `Settings`（`base.py`）增加同名 `str = ""` 字段。
3. 是否加入 `_SANDBOX_AFFECTED_SETTINGS`：
   - **描述仅用于 prompt 注入、不改 adapter 构造参数**：可不进该集合；`refresh_settings` 仍会 `setattr(settings, key, value)`，下次读 `settings.XXX` 即生效。
   - **若希望与其它沙箱键行为一致、并触发 manager soft-reset**：可一并加入；对描述本身无害，但会丢弃 manager 缓存（soft，不杀运行中沙箱）。
4. i18n：`settingDesc.<KEY>` en/zh（及现有其它 locale 若要求同步）。
5. 注入链路读当前 platform 对应的 description（空则跳过）。

### 推荐 B：平台无关单键

| Key | type | subcategory | depends_on |
|---|---|---|---|
| `SANDBOX_IMAGE_DESCRIPTION` | TEXT | general | `ENABLE_SANDBOX` |

优点：一处维护；缺点：与「描述绑定镜像/模板」语义弱，换 platform 时文案不随镜像走。

### 是否应对称 E2B_TEMPLATE_DESCRIPTION

- **模式上应支持**：现有 definition/depends_on/subcategory/i18n 完全对称，零新控件类型。
- **产品上**：PRD Open Q2 要求确认；最小闭环可只做 OpenSandbox，E2B/Daytona 预留键名或一并加上（空默认、无注入成本）。
- 命名：E2B 侧实体是 **template** 而非 image，故用 `E2B_TEMPLATE_DESCRIPTION` 比 `E2B_IMAGE_DESCRIPTION` 更贴合现有 `E2B_TEMPLATE`。

## Persistence & hot-reload behavior

### 写入路径

1. 前端 `handleSave` → settings API。
2. `SettingsService.set` → `SettingsStorage.set` 写 Mongo（`_id=key`）。
3. `refresh_settings(key)`：
   - `_settings_cache[key] = value`
   - `setattr(settings, key, value)` → **runtime 立即可见**
   - 若 key ∈ `_SANDBOX_AFFECTED_SETTINGS` → `reset_session_sandbox_manager()`（单例置 None）
4. `requires_restart` 来自 `key in RESTART_REQUIRED_SETTINGS`；当前**全部沙箱键均不在**该集合（见 `test_sandbox_settings_do_not_require_restart`）。

### `_SANDBOX_AFFECTED_SETTINGS` 全集

`ENABLE_SANDBOX`, `SANDBOX_PLATFORM`, 全部 `DAYTONA_*`, 全部 `E2B_*`, 全部 `OPENSANDBOX_*`（含 IMAGE/DOMAIN/API_KEY/TIMEOUT/WORK_DIR/USE_SERVER_PROXY）。见 `src/kernel/config/service.py` L48–69。

### Soft reset 语义（`reset_session_sandbox_manager`）

- 丢弃 manager 单例与内存 cache。
- **不清** Mongo user_bindings，**不停止**运行中沙箱。
- 下次 `get_or_create` 用新 settings 建 manager/adapter；可能经 binding 的 sandbox_id 重连，失败则新建。
- 注释明确：平台切换时旧平台沙箱由 provider TTL 回收。

### 对「能力描述」字段的含义

| 场景 | 行为 |
|---|---|
| 保存描述后，`settings.OPENSANDBOX_IMAGE_DESCRIPTION` | 立即更新（只要 refresh 路径走通） |
| 已在跑的 sandbox 容器 | 不受影响（描述不进容器创建参数） |
| 下次 agent 构建 system prompt 时若读 settings | **可立刻用上新描述**（与 session 是否已有 sandbox 无关） |
| 改 `OPENSANDBOX_IMAGE` 本身 | soft-reset manager；**已绑定 session 的沙箱镜像不热换**；新 session / 重建才用新镜像 |
| `AGENT_HARNESS_MODE` | **必须重启**（`RESTART_REQUIRED_SETTINGS` + 文案写 restart required） |

### 现有 sandbox prompt 注入点（描述将落脚处）

- 常量：`SANDBOX_SYSTEM_PROMPT` / `SANDBOX_RUNTIME_SECTION`（`search_agent/prompt.py`，team 复用 search 版）。
- 构建：`search_agent/nodes.py` 沙箱分支返回 `SANDBOX_SYSTEM_PROMPT`，并在有 `work_dir` 时 append `SANDBOX_RUNTIME_SECTION.format(work_dir=...)`。
- **当前无** 镜像能力描述 section；注入应紧邻上述 section，空字符串不注入。

## i18n checklist

### `settingDesc.*` 命名空间

- 位置：`frontend/src/i18n/locales/en.json` / `zh.json` 下 `"settingDesc": { ... }`（约 L1941+）。
- 定义侧 description 字段写 **i18n key 字符串**，如 `"settingDesc.OPENSANDBOX_IMAGE"`；面板用 `t(setting.description)` 渲染。

### 已有沙箱相关 settingDesc（en/zh 均有）

- `ENABLE_SANDBOX`, `SANDBOX_PLATFORM`, `SANDBOX_GREP_TIMEOUT`, …
- `DAYTONA_*`（含 `DAYTONA_IMAGE`）
- `E2B_*`（含 `E2B_TEMPLATE`）
- `OPENSANDBOX_*`（DOMAIN/API_KEY/IMAGE/TIMEOUT/WORK_DIR/USE_SERVER_PROXY）
- `AGENT_HARNESS_MODE`（文案含「需重启 / restart required」）

### 已有 subcategory 标签

`subcategories` 块：

- `daytona`: "Daytona"
- `e2b`: "E2B"
- `opensandbox`: "OpenSandbox"
- `general`, `cache`, `title`, …

**新增描述键只需补 `settingDesc.<NEW_KEY>`**，subcategory 标签可复用，不必新增 subcategory。

### 新增键 i18n 模板（示例）

| key | en | zh |
|---|---|---|
| `settingDesc.OPENSANDBOX_IMAGE_DESCRIPTION` | Capability/limits description of the OpenSandbox image; injected into the agent system prompt when non-empty | OpenSandbox 镜像能力边界描述；非空时注入 agent system prompt |
| `settingDesc.E2B_TEMPLATE_DESCRIPTION` | Capability/limits description of the E2B template; injected when non-empty | E2B 模板能力边界描述；非空时注入 |
| `settingDesc.DAYTONA_IMAGE_DESCRIPTION` | Capability/limits description of the Daytona image/snapshot; injected when non-empty | Daytona 镜像/快照能力边界描述；非空时注入 |

其它 locale（`ja`/`ko`/`ru`）若仓库要求齐全，实现阶段按现网惯例同步。

## Reference implementations

### 1. 设置系统长文本（首选样板）

**`SESSION_TITLE_PROMPT`** — `definitions.py` L297–303：

- `type: SettingType.TEXT`
- `category: SESSION`, `subcategory: "title"`
- 长 default 多行字符串（含 `\n`）
- 无 `frontend_visible` / `depends_on` / `requires_restart`
- storage 按 text → str 持久化
- UI：SettingsPanel `type === "text"` → textarea 8 行

这是**唯一**且完整的「settings 多行文本」端到端样板。

### 2. Persona 长文本 UI（非 settings，形态参考）

`frontend/src/components/persona/PersonaEditorModal.tsx`：

- `description`：单行 `<input>`（简介）
- `system_prompt`：`<textarea rows={8} className="ppe-textarea">` + 字数计数
- 数据落在 persona preset Mongo，**不是** `SETTING_DEFINITIONS`

适用于「独立表单」场景；沙箱能力描述应走 settings 系统，优先对齐 `SESSION_TITLE_PROMPT`，不必新建面板。

### 3. MCP server「描述」

`src/kernel/schemas/mcp.py` / `MCPServerForm.tsx`：**无** 面向运维的 freeform server description 编辑字段；工具列表里的 `tool.description` 来自 MCP 协议，不是 admin 长文本设置。**不宜**作为本需求样板。

### 4. 平台依赖 + 镜像 STRING 样板

`OPENSANDBOX_IMAGE` / `E2B_TEMPLATE` / `DAYTONA_IMAGE`：depends_on + subcategory 模式，描述字段应**紧挨**对应镜像/模板键定义，便于 UI 分组展示。

### 5. 热更新样板

- `tests/kernel/config/test_sandbox_setting_refresh.py`
- `src/kernel/config/service.py` `_SANDBOX_AFFECTED_SETTINGS` + `_reset_sandbox_runtime_state`
- `src/infra/sandbox/session_manager.py` `reset_session_sandbox_manager`

## Key files

| Path | Role |
|---|---|
| `src/kernel/schemas/setting.py` | `SettingType` / `SettingItem` / `depends_on` schema |
| `src/kernel/config/definitions.py` | `SETTING_DEFINITIONS`（SANDBOX 块 + `SESSION_TITLE_PROMPT` TEXT 样板 + `AGENT_HARNESS_MODE`） |
| `src/kernel/config/base.py` | runtime `Settings` 字段（镜像/模板默认值） |
| `src/kernel/config/constants.py` | `RESTART_REQUIRED_SETTINGS`（含 `AGENT_HARNESS_MODE`，不含沙箱键） |
| `src/kernel/config/service.py` | `refresh_settings`、`_SANDBOX_AFFECTED_SETTINGS` |
| `src/infra/settings/storage.py` | 持久化与 type 校验（text=str） |
| `src/infra/settings/service.py` | set 后 refresh；`requires_restart()` |
| `src/infra/sandbox/session_manager.py` | adapter 读 image/template；soft reset |
| `src/agents/search_agent/prompt.py` | `SANDBOX_SYSTEM_PROMPT` / `SANDBOX_RUNTIME_SECTION` |
| `src/agents/search_agent/nodes.py` | 沙箱 prompt 组装与注入 |
| `frontend/src/types/settings.ts` | 前端 SettingType 联合类型 |
| `frontend/src/components/panels/SettingsPanel.tsx` | type→控件映射（含 text textarea） |
| `frontend/src/components/panels/SettingsPanel.constants.ts` | TYPE_COLORS、category 顺序 |
| `frontend/src/i18n/locales/en.json` / `zh.json` | `settingDesc.*`、`subcategories.*` |
| `frontend/src/components/persona/PersonaEditorModal.tsx` | 非 settings 的长文本 UI 参考 |
| `tests/kernel/config/test_sandbox_setting_refresh.py` | 沙箱热更新契约 |

## Caveats / Not Found

- **未找到** 任何现有 `*_IMAGE_DESCRIPTION` / `E2B_TEMPLATE_DESCRIPTION` / `SANDBOX_*_DESC` 配置键或 prompt 注入逻辑。
- **未找到** Setting 定义层对 STRING/TEXT 的 max length 约束。
- **未找到** SettingsPanel 对 `is_sensitive` TEXT 的特殊处理差异（sensitive 主要影响 API mask `********`）。
- MCP 无 admin 可编辑的「server 长描述」字段，不能当 settings textarea 样板。
- Persona `description` 是短 input，真正的长文本是 `system_prompt`，且不走 settings 持久化。
- 描述热更新是否加入 `_SANDBOX_AFFECTED_SETTINGS` 属于实现选择；**仅 prompt 读取则不必**，但加入也不会要求重启。
- 已创建 sandbox session 的**镜像本身**不会因改 settings 而热换；与「描述注入」是两条链路。
- `AGENT_HARNESS_MODE` 仍在 LLM/cache 子类、且需重启；本文件聚焦镜像描述字段，Harness UI 迁移属同一任务另一需求，可另文或 design 覆盖。
