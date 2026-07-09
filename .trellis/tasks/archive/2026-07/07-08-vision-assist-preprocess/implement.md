# Implement: 辅助 vision 模型预处理注入

## 执行顺序

### Step 1：配置层（后端）

- [ ] `src/kernel/schemas/setting.py`：加 `VISION_ASSIST = "vision_assist"` 到 `SettingCategory`
- [ ] `src/kernel/config/base.py`：加 `ENABLE_VISION_ASSIST: bool = False` / `VISION_ASSIST_MODEL_ID: str = ""` / `VISION_ASSIST_MAX_BYTES: int = 10 * 1024 * 1024`
- [ ] `src/kernel/config/definitions.py`：加三个 setting 定义（category=VISION_ASSIST，`VISION_ASSIST_MODEL_ID` 的 `depends_on=ENABLE_VISION_ASSIST`、`frontend_visible=True`）
- **验证**：`.venv/Scripts/python.exe -c "from src.kernel.config import settings; print(settings.ENABLE_VISION_ASSIST, settings.VISION_ASSIST_MODEL_ID, settings.VISION_ASSIST_MAX_BYTES)"` 输出 `False "" 10485760`

### Step 2：核心模块

- [ ] 新增 `src/agents/core/vision_assist.py`（按 design.md 实现 `describe_image` + `describe_image_attachments`）
- [ ] 提取 `src/agents/core/node_utils.py` 的 size 格式化为 `_format_size` 辅助函数（复用，不改原行为）
- **验证**：`.venv/Scripts/python.exe -c "from src.agents.core.vision_assist import describe_image_attachments; print('ok')"`

### Step 3：node_utils 渲染

- [ ] `src/agents/core/node_utils.py:_format_attachment_summary`：加 `vision_description` 渲染分支（有描述渲染描述块，无描述维持现状）
- [ ] `build_human_message`：确认 `supports_vision=False` 分支无需改（image attachment 有描述仍进 `text_summary_attachments`，由 `_format_attachment_summary` 渲染）
- **验证**：单元测试 `test_format_attachment_summary_with_vision_description`

### Step 4：三节点接入

- [ ] `src/agents/search_agent/nodes.py:311-316`：加 `else` 分支调 `describe_image_attachments`
- [ ] `src/agents/fast_agent/nodes.py:278-283`：同上
- [ ] `src/agents/team_agent/nodes.py:546-551`：同上
- [ ] 三处 import `from src.agents.core.vision_assist import describe_image_attachments`
- **验证**：`.venv/Scripts/python.exe -m pytest tests/agents/ -q`（现有测试不破）

### Step 5：测试

- [ ] 新增 `tests/agents/core/test_vision_assist.py`（8 个用例，见 design.md 测试策略）
- [ ] 扩展 `tests/agents/core/test_node_utils.py`（若有）或新建：`_format_attachment_summary` 带/不带描述
- **验证**：`.venv/Scripts/python.exe -m pytest tests/agents/core/test_vision_assist.py -q` 全绿

### Step 6：前端

- [ ] `frontend/src/components/panels/SettingsPanel.constants.ts`：`MODEL_ID_SETTINGS` 加 `VISION_ASSIST_MODEL_ID`（kind=`"chat"`）
- [ ] `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`：加三个 setting 描述 + VISION_ASSIST 分类标题
- **验证**：前端构建通过，Settings 页能显示 vision_assist 区段

### Step 7：端到端验证

- [ ] 配置一个 vision 模型卡片（如 `gpt-4o-mini`，`kind=chat`，`profile.supports_vision=true`）
- [ ] Settings 页设 `VISION_ASSIST_MODEL_ID` = 该卡片，开 `ENABLE_VISION_ASSIST`
- [ ] 主模型保持 `deepseek-v4-flash`（`supports_vision=false`）
- [ ] Web 端发图片 → agent 回复识别出图片内容
- [ ] WeCom 端发图片 → 同上
- [ ] 关 `ENABLE_VISION_ASSIST` → 行为同现状（文本摘要）
- [ ] 主模型切 vision 模型 → 直通 `image_url` block（回归）

## 验证命令

```powershell
# 后端测试
.venv/Scripts/python.exe -m pytest tests/agents/core/test_vision_assist.py -q
.venv/Scripts/python.exe -m pytest tests/agents/ -q

# 配置项生效
.venv/Scripts/python.exe -c "from src.kernel.config import settings; print(settings.ENABLE_VISION_ASSIST, settings.VISION_ASSIST_MODEL_ID, settings.VISION_ASSIST_MAX_BYTES)"

# 前端构建
cd frontend; pnpm build
```

## 风险点 / 回滚

| 风险 | 回滚 |
|---|---|
| 三节点接入引入 bug | `else` 分支独立，回滚 = 删 else 分支 |
| `_format_attachment_summary` 改动破坏现有摘要 | 回滚 = 删 `vision_description` 分支，恢复原循环 |
| 辅助模型未配置时误调用 | `describe_image_attachments` 前置检查 `ENABLE_VISION_ASSIST` + `VISION_ASSIST_MODEL_ID`，双保险 |
| 前端 i18n 缺漏 | 构建时 i18n 校验会报 |

## 关键文件

- 新增：`src/agents/core/vision_assist.py`、`tests/agents/core/test_vision_assist.py`
- 改：`src/agents/core/node_utils.py`、`src/agents/search_agent/nodes.py`、`src/agents/fast_agent/nodes.py`、`src/agents/team_agent/nodes.py`
- 配置：`src/kernel/schemas/setting.py`、`src/kernel/config/base.py`、`src/kernel/config/definitions.py`
- 前端：`frontend/src/components/panels/SettingsPanel.constants.ts`、`frontend/src/i18n/locales/*.json`

## 实现前确认

- [ ] 前端 `MODEL_ID_SETTINGS` kind=`"chat"` 能否过滤 `supports_vision=true` 卡片（若不能，下拉显示全部 chat 卡片，管理员自选——MVP 可接受）
- [ ] `LLMClient.get_model(model_id=...)` 返回的 chat 模型实例支持 `ainvoke([HumanMessage(content=[{type:image_url...}])])`（LangChain 标准，应支持）
- [ ] 辅助模型 `ainvoke` 返回 `AIMessage.content` 格式（str 或 list）——已兼容两种
