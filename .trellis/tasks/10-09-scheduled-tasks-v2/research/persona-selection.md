# R1：定时任务选择专家（Persona）

## 结论摘要
1. 一期已预留 `persona_preset_id` 字段，但 UI 没入口、service 不校验，runner 的专家路径没有测试覆盖（测试把 `_execute` 整体 mock 掉了，`test_service_runner.py:89-102`）。
2. runner 有 3 处缺陷，二期必须修：①不应用 Dify 知识库；②专家绑定的 skill 不生效；③专家被删或下架时，用户看到英文错误码，并被重试和计入自动暂停。
3. 创建、修改任务时应复用 `PersonaPresetManager.get_preset(...)`（`manager.py:213`）校验。不要用 `use_preset`，它会累加使用次数。
4. 前端复用 `PersonaPresetSelector` + `usePersonaPresets`。卡片上的专家名称和头像由后端在响应中补充。
5. 模型选择后端已支持，是顺手做的范围。智能体模式和团队不做：选了专家，模式由专家决定；团队与专家互斥，且需要新增字段。

## 现状（附证据）
**来源与可见性（已验证）**
- 列表接口是 `GET /api/persona-presets/`，需要 `persona_preset:read`（`persona_preset.py:62-77`）。管理员的判定是持有 `persona_preset:admin`（`persona_preset.py:36-37`）。
- 可见规则在 `manager.py:129-142`（`_can_view`）：
  - 自建专家（user scope）：仅所有者可见，草稿和归档状态也可用。
  - 全局专家：普通用户只能看到 `public + published`；管理员能看到全部全局专家。
- 探针确认：已归档的全局专家，普通用户不可见，管理员可见。
- 「广场安装」实际是 `copy_preset`（`manager.py:~357`），复制后成为自建专家，没有单独的安装关系。
- `use_preset`（`manager.py:390-401`）会增加 `usage_count` 并更新用户偏好，副作用不小。

**专家对一次运行的影响（已验证）**
- 专家没有模型字段（`PersonaPresetCreate.model_fields` 探针结果），所以不存在模型冲突。
- `resolve_persona_request`（`chat.py:252`）负责：
  - 写入 `persona_system_prompt`；
  - 注入 skill 提示（`_persona_skill_hints`，由 `search_agent/nodes.py:188` 消费）；
  - 返回 snapshot。
- Dify 知识库由 `chat.py:403-408` 另外调用 `apply_dify_kb_dataset_ids_to_agent_options`（消费方是 `dify_kb_tool.py:106`）；企业微信也调用了它。
- `enabled_skills` 在后端没有生成。Web 端由前端 `useAgent.ts:645` + `personaRequestConfig.ts` 把 `snapshot.skill_names` 当作 `enabled_skills` 发送。后端默认值是 `None`，含义是不限制，使用用户全部技能。
- 智能体：`resolve_persona_agent_id`（`persona.py:29`）里专家的 preferred 优先，取值只有 fast 或 search。选了团队会清掉专家（`chat_validation.py:7-12`）。
- chat 的顺序是先解析专家、再校验模型（`chat.py:400-403`）；runner 相反（`runner.py:240-250`）。因为专家无模型，只影响哪个错误先报出，无实质影响。`agent_options` 是同一个 dict（探针确认），数据不会丢。

**runner 核对（已验证）**
- agent_id：`runner.py:253-259` 正确，专家的 preferred 优先。
- metadata：`runner.py:273-278` 写入 `persona_preset_id`、`persona_preset_name`、`persona_snapshot`，前端 `useAgent.ts:381-389` 与 `useSessionConfig.ts:288` 能还原专家。
  - 缺少 `persona_avatar`（`chat.py:244` 有），分享页会受影响。
  - 缺少 `enabled_skills`、`disabled_*`、`language` 等（`build_conversation_config`，`chat.py:219`）。
- 失败文案：专家被删、下架或用户失权时，`use_preset` 抛 `NotFoundError("persona_preset_not_found")`。`_friendly_error`（`runner.py:400`）没有对应映射，探针输出就是 `'persona_preset_not_found'`。
  - 该错误会重试（`runner.py:181`），连续 3 次失败后自动暂停，原因文案仍是「请检查任务内容」。
- runner 没有 Dify 相关调用（grep 结果为 0）。

## 缺口
1. `service.create_task` / `update_task`（`service.py:77,105`）不校验专家。
2. `ScheduledTaskCreate.agent_id` 是任意字符串（schema:78），后续 `AgentFactory` 对未知值如何处理未验证，应收窄。
3. 响应中没有专家的名称和头像。
4. 前端类型已有 `persona_preset_id`（`scheduledTask.ts:36,61`），`TaskEditorDialog.save` 没有提交。

## 推荐方案
**后端**
- 新增 `src/infra/scheduler/persona.py`，包含：
  - `async ensure_persona_usable(preset_id, user)`：内部调用 `PersonaPresetManager().get_preset(id, user_id=user.sub, is_admin="persona_preset:admin" in user.permissions)`。捕获 `NotFoundError` 后抛 `ScheduledTaskPersonaError("所选专家不存在或你无权使用")`。
  - `async persona_brief(ids, user)`：返回 `{id: {name, avatar, available}}`，给响应使用。
- 路由 `create_task` / `patch`（`scheduled_task.py:67,89`）把 `ScheduledTaskPersonaError` 转成 400。service 签名新增 `actor: TokenPayload`。
  - update 时若 `persona_preset_id` 在 `updates` 中且非空才校验。
  - `null` 表示清除专家，前端必须显式发送 null（`exclude_unset` 语义）。
- `ScheduledTaskResponse` 增加 `persona_name`、`persona_avatar`、`persona_available`。列表按去重后的 id 并发调用 `get_preset`，任务上限 20 个，成本可控。
- runner 的 `_execute`：
  - 增加 `apply_dify_kb_dataset_ids_to_agent_options(agent_options_final, persona_snapshot=...)`，且无专家也要调用，行为与 chat 一致（会带入系统默认知识库）。
  - 专家存在且 `skill_names` 非空时，传 `enabled_skills=snapshot.skill_names`，对齐 Web 的行为。
  - 捕获专家解析阶段的 `NotFoundError` / `AuthorizationError`，返回 `FAILED` 和固定文案「所选专家已被删除、下架或你已无权使用，请编辑任务更换专家」。该错误不重试，直接暂停并写入专属 `pause_reason`（给 `_monitor` 增加不可重试标记）。
  - metadata 改为复用 `build_conversation_config`，再叠加定时任务自己的字段，补齐 `persona_avatar`。
- 契约：`persona_preset_id` 为空字符串或 null 都表示不使用专家；执行时每次取最新版专家（跟随最新，不固定版本）。

**前端**
- `TaskEditorDialog` 在第 1 步下方增加「执行专家（可选）」行，展示头像、名称和「更换专家」按钮。点击后打开 `PersonaPresetSelector`（`ChatInputSelectors.tsx:159` 的用法）。
  - `onUsePreset` 不调用 `/use` 接口，只记录所选专家，并用 preset 拼出 snapshot 返回。
  - 需要把 `usePersonaPresets` + 分页、搜索、标签的接线从 `ChatAppContent.tsx:119-149` 抽出，或复制过来。
  - 没有 `persona_preset:read` 权限的用户隐藏该行，与 `ChatAppContent.tsx:101` 一致。
- 保存时提交 `persona_preset_id`（清除时为 null）。
- `TaskCard`（`index.tsx` 的 `TaskCard`）使用 `PersonaAvatarWithLoading` 显示头像和名称；`persona_available=false` 时显示「专家已不可用」徽标。
- 类型：`ScheduledTask` 增加三个字段；i18n 在 `zh.json:3108`、`en.json:3108` 的 `scheduledTask` 下新增 `persona.label`、`persona.none`、`persona.unavailable`、`persona.hint`；复用 `personaPresets.change`、`personaPresets.title`、`personaPresets.clear`。ja/ko/ru 现无 `scheduledTask`，保持与一期一致。

## 备选方案与权衡
- **卡片展示专家**：任务文档里冗余存名称和头像，无需联表，但改名后不同步，且无法判断是否可用；前端逐个请求 `GET /{id}`，请求数多，且 404 时无法区分原因。已推荐的后端补充方案在可用性判断上最准确。
- **专家失效时降级为无专家继续执行**：不推荐，会产出风格与权限边界都不对的结果。
- **自建轻量下拉替代 Selector**：改动小，但丢失搜索、分页和预览，后续要重做，不推荐。
- **模型选择**：后端已支持（`runner.py:240-242` + 友好文案 `runner.py:402-405`）；仅需前端模型列表组件，工作量约 0.5 天，可顺手做。
- **智能体模式 / 团队**：不做。有专家时模式由专家决定；search 模式还需要沙箱准入（`chat.py:~425`，runner 没有）；团队需要 `team_id` 新字段，并与专家互斥（`chat_validation.py:7-12`）。

## 风险
- 每次运行都会调用 `use_preset`，累加 `usage_count` 并更新用户「最近使用」，需要产品确认可以接受。
- 专家被作者修改后，定时任务输出会随之变化（跟随最新的语义）。
- 专家绑定的 skill 被下架时，`_resolve_skill_hints` 会静默丢弃该提示（`manager.py:~95-125`），不会报错。
- 沙箱准入和并发限制（`limiter.acquire`）在 runner 路径上没有，选了 search 专家时更明显（见跨课题发现）。

## 测试建议
- 在 `conftest.py` 增加 `FakePersonaStorage`（`get_by_id`、`increment_usage`、`touch_user_preference`）和 `FakeMarketplace`，用 monkeypatch 替换 `src.api.routes.chat.PersonaPresetManager`，让 `resolve_persona_request` 走真实逻辑。
- 再增加 `FakeTaskManager`（记录 `submit` 的 kwargs，`get_run_status` 返回 COMPLETED）、`fake_owner`（替换 `_resolve_owner`）、`FakeSessionManager`，并在 `fakes` fixture 中 monkeypatch。
- 用例：
  - 专家 preferred=search 时 agent_id 和 `persona_system_prompt` 正确；
  - snapshot、`persona_avatar` 写入 metadata；
  - `skill_names` 非空时 `enabled_skills` 传递；
  - Dify 的 `agent_options` 注入；
  - 专家被删、下架（全局 + archived）、失权时的固定文案、不重试、不计入连续失败的特殊处理；
  - 创建和修改时的 400 错误，以及他人的自建专家不可选；
  - 清除专家（null）语义。
- 前端：用 `node:test` 为「选中专家后保存请求体」写一个小型契约测试（参照 `personaRequestConfig.test.ts`）。

## 需要产品决策的问题
1. 专家被删或下架后，任务是否直接暂停？推荐直接暂停并给出明确原因，不重试，也不降级为无专家执行。理由：静默降级会产出错误的结果。
2. 专家被作者修改后，任务是否跟随最新版本？推荐跟随最新，与聊天中的行为一致，也无需另建版本管理。
3. 选了专家后，是否同时开放模型选择？推荐本期开放，因为后端已就绪；智能体模式和团队不开放。
4. 每次运行累加 `usage_count` 是否可以接受？推荐可以，与普通会话统计口径一致（需求 6）。

## 跨课题发现
- ask_human（课题 R2）：`runner.py` 目前没有任何 ask_human 隔离。
- 并发与沙箱：`TaskManager.submit` 路径没有 `limiter.acquire` 和沙箱准入，但结束时有 `release`（`manager.py:503,722`），计数可能失衡。
- 清理逻辑：runner 写入的 `hidden_from_conversation_list` 会让这些会话不出现在列表里，需与 checkpoint 清理课题一起确认。
- 分享页：`share.py:361` 读取 `persona_avatar`，runner 目前不写入。
