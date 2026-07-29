# 公开 Persona 的 Marketplace Skill 依赖与临时挂载

## Goal

公开 Persona 时，将其挂载的个人 Skill 经创建者确认后同步发布到现有 Marketplace；其他用户使用该 Persona 时，在当前 Persona 会话中临时获得这些公开 Skill 的描述注入和文件读取能力，但不把 Skill 永久安装进个人空间。

## Background and Confirmed Behavior

- 普通用户只能看到自己的 `scope=user` Persona；跨用户公开只对 `scope=global + visibility=public + status=published` 生效，创建 global Persona 需要管理员权限（`src/infra/persona_preset/manager.py:31-44,55-80`；`src/infra/persona_preset/storage.py:390-414`）。
- Persona 列表和详情返回的 `PersonaPreset` 包含 `system_prompt`、`skill_names` 和 `dify_kb_dataset_ids`；广场预览也显示系统提示词和逐个 Skill 名称（`src/kernel/schemas/persona_preset.py:137-155`；`frontend/src/components/persona/PersonaPreviewSidebar.tsx:192-224`）。
- 私有 Skill 详情和文件接口始终按当前用户 `user.sub` 查询，因此公开 Persona 不会直接授权读取创建者的私有 Skill 文件（`src/api/routes/skill.py:345-355,414-427`）。
- 当前使用 Persona 时，后端只按名称把 Persona 的 `skill_names` 与使用者自己已安装、启用的 Skill 求交集，缺失项进入 `missing_skill_names`（`src/infra/persona_preset/manager.py:267-305`）。
- 聊天入口将上述交集作为 `enabled_skills`，而 `/skills/` backend 仍绑定使用者自己的 `user_id`（`src/api/routes/chat.py:257-277`；`src/infra/backend/skills_store.py:119-193`）。
- 前端忽略 `missing_skill_names`，即使能力残缺仍显示使用成功（`frontend/src/components/persona/usePersonaPlaza.ts:165-197`）。
- 复制公开 Persona 只复制名称和配置，不复制或安装 Skill（`src/infra/persona_preset/manager.py:231-265`）。
- Marketplace Skill 已支持公开查看文件和显式安装，但 Persona 当前没有保存或校验 Marketplace 依赖（`src/api/routes/marketplace.py:230-272,275-329`）。
- 当前纯名称匹配不校验来源，使用者的同名手工 Skill 可能被误当成 Persona 所需 Skill。

## Product Decisions

- D1：公开 Persona 采用“创建/发布时同步发布个人 Skill，使用时会话级临时挂载”的交付模型。
- D2：同步发布的 Skill 直接进入现有 Marketplace，允许公开查看、搜索和独立安装；不增加 `persona_only` 等新可见性。
- D3：Marketplace Skill 名称全局唯一。同名异源一律阻止，不做 Persona 版本或用户版本的静默覆盖；只有用户本地 Skill 能证明安装自同一个 Marketplace Skill 时才视为同一依赖。
- D4：Persona 临时挂载与现有 `install_skill -> sandbox/work_dir/temp_skills` 是两条独立链路，本任务不修改、复用或耦合后者。

## Requirements

- R1：创建或发布 public/global Persona 时，后端预检所有挂载 Skill 的本地所有权、完整性、Marketplace 发布状态和全局名称冲突。
- R2：存在未发布的个人 Skill 时，前端弹窗逐项列出，并明确告知同步后将公开出现在 Marketplace、可查看且可独立安装；未经确认不得公开 Persona。
- R3：确认后协调发布全部缺失 Skill，再保存 Persona 的稳定 Marketplace 依赖引用；任一步失败都不得留下“依赖不完整但 Persona 已公开”的状态。
- R4：Marketplace 名称已被异源 Skill 占用时，不得覆盖元数据或文件，必须返回结构化冲突信息并提示创建者验证或重命名。
- R5：public/global Persona 运行时只解析公开、有效的 Marketplace 依赖；不得退回使用来源不明的同名本地 Skill。
- R6：使用者存在同名永久 Skill 时，仅当其安装元数据指向同一个 Marketplace Skill 才允许启用；手工或来源不明的同名 Skill 必须阻止 Persona 启用并列出冲突名称。
- R7：Persona Marketplace Skill 以只读方式临时挂载到当前会话的虚拟 `/skills/`，参与正常 Skill 描述注入和文件读取，但不得写入使用者的个人 Skill 集合、元数据、列表或缓存。
- R8：清除 Persona、切换 Persona、结束会话或依赖失效后，临时 Skill 不再可见；会话恢复只根据依赖引用重建，不在会话元数据保存完整文件内容。
- R9：缺失、停用、不完整或冲突依赖必须阻止启用并在前端明确反馈，不得静默跳过后继续显示成功。
- R10：历史 public/global Persona 只有 `skill_names` 时，通过 Marketplace 同名记录做兼容解析；解析失败则阻止使用，绝不匹配使用者的任意同名手工 Skill。
- R11：private/user Persona 原有的本地 `skill_names` 行为保持兼容。
- R12：现有 Marketplace 列表、详情、文件读取、永久安装接口及 Agent 主动安装到 sandbox `temp_skills` 的行为保持不变。

## Acceptance Criteria

- [ ] AC1：发布 public/global Persona 前可得到 ready、requires_publish、conflicts 三类结构化预检结果。
- [ ] AC2：未发布个人 Skill 必须经一次显式确认才会公开同步到 Marketplace。
- [ ] AC3：批量同步或 Persona 保存任一步失败时，Persona 不会公开为依赖不完整状态，新建发布记录按补偿规则处理。
- [ ] AC4：Marketplace 同名异源冲突不覆盖任何文件，并在 Persona 编辑器中显示准确名称和处理提示。
- [ ] AC5：其他用户启用公开 Persona 后，临时 Skill 描述进入系统提示词，Agent 可通过虚拟 `/skills/{name}/...` 读取公开文件。
- [ ] AC6：临时挂载不会在使用者的个人 Skill 集合、Skill 列表、元数据或持久缓存中新增记录。
- [ ] AC7：使用者的同名手工/来源不明 Skill 会阻止 Persona 启用；安装自同一 Marketplace Skill 的同名 Skill 不报冲突。
- [ ] AC8：缺失、停用或不完整依赖会返回结构化错误，前端不再显示误导性的“使用成功”。
- [ ] AC9：清除、切换 Persona 和会话恢复均遵守临时依赖生命周期。
- [ ] AC10：历史公开 Persona 不会执行使用者的任意同名本地 Skill。
- [ ] AC11：private/user Persona 和公开 Persona 的合法使用路径均有自动化回归测试。
- [ ] AC12：现有 Marketplace 永久安装及 `install_skill -> sandbox/temp_skills` 测试保持通过。
- [ ] AC13：权限矩阵和代码证据保存在 `research/visibility-audit.md`，技术边界和执行顺序分别保存在 `design.md`、`implement.md`。

## Out of Scope

- 不增加 Marketplace 的新可见性状态。
- 不提供私有、加密或黑盒 Skill 分发。
- 不新增 Marketplace 历史版本文件仓库；本期以全局唯一 Marketplace 名称作为依赖身份，版本字段用于展示和诊断。
- 不修改 Agent 主动安装 Marketplace Skill 到 sandbox `temp_skills` 的链路。
- 不修改与本问题无关的企业微信部署文件。
