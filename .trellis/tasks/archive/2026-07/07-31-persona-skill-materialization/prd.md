# Persona Skill 使用时实体化到用户空间

## Goal

统一 Persona Skill 的数据模型：Persona 只声明 Marketplace Skill 依赖；用户使用 Persona 时，系统确保依赖已真实安装到该用户的普通 Skill 空间。Agent 后续只通过现有用户 Skill 存储加载、读取和 transfer，不再依赖“提示词可见、文件读取另走 overlay”的临时挂载双轨。

## Background

- 2026-07-29 的原方案为了避免用户 Skill 空间臃肿，采用 `PersonaSkillStorageOverlay` 做会话级临时挂载。
- 实测证明双轨模型会造成能力不一致：`read_file`/`ls` 可读取 Marketplace overlay，但 `transfer_file`/`transfer_path` 的批量下载落回用户 SkillStorage，其他用户没有本地副本时返回 `file_not_found`。
- 当前 `POST /api/persona-presets/{id}/use` 已负责校验 Marketplace 依赖及用户同名冲突，是执行“确保已安装”的自然事务边界。
- 当前 Marketplace 安装链路已能把文件复制到用户 SkillStorage，并写入 `installed_from=marketplace`；应抽取并复用领域服务，不能让 Persona manager 调用 HTTP route。
- 用户已明确接受从“临时挂载、不污染用户空间”改为“使用 Persona 时下载到用户自己的 Skill 空间”，以换取统一、可预测的运行时行为。

## Requirements

1. 公开 Persona 保存时：
   - 个人 Skill 仍需先同步发布到 Marketplace；
   - Persona 持久化 Marketplace Skill 引用；
   - 同名但无法证明同源时继续硬拦截，Marketplace 不允许同名异物。
2. 用户使用公开 Persona 时，对每个依赖执行幂等的 ensure-installed：
   - 用户空间没有同名 Skill：从 Marketplace 复制完整文件并写 `installed_from=marketplace`；
   - 用户空间只要存在同名 Skill，无论是手工创建、Marketplace 安装还是来源不明，均直接复用，不比较来源、版本或文件内容，也不重复复制；
   - 仅当本地没有同名项而 Marketplace Skill 不存在、停用或缺少 `SKILL.md` 时，Persona 启用失败，不进入聊天。
3. 所有依赖全部确保成功后才返回 Persona snapshot、增加使用次数并切换 Persona。
4. 失败不得留下没有 metadata 的半安装 Skill；多 Skill 安装先完整预检缺失依赖，复制异常时补偿清理本次未完成项，用户原有 Skill 永不回滚。
5. Persona snapshot 和三类 Agent 不再需要 `persona_marketplace_skills` overlay 来提供文件；`skill_names` 直接指向用户空间中的普通 Skill。
6. `read_file`、`ls`、`transfer_file`、`transfer_path`、Skill prompt 必须从同一用户 SkillStorage 得到一致结果。
7. 修复官方 Persona 同步发布确认可能重复创建的问题：前端同步防重入，后端创建幂等。
8. 兼容已存在且保存了 `marketplace_skills` 的公开 Persona；不要求管理员重新保存。
9. Persona 自动安装的 Skill 永久保留在用户空间，作为普通 Skill 显示在用户 Skills 列表中；离开或切换 Persona 时不自动卸载。
10. 保持现有 `disabled_skills` 语义：Persona 负责确保同名 Skill 存在并设置 `enabled_skills` 白名单，但不擅自修改用户已有的禁用偏好。

## Acceptance Criteria

- [ ] 新用户首次使用带 Marketplace Skills 的官方 Persona，所有缺失依赖被安装到该用户 SkillStorage，随后 Persona 启用成功。
- [ ] 同一用户再次使用同一 Persona 不重复写文件、不产生重复 metadata，也不报“already installed”。
- [ ] 自动安装后的 Skill 可被 prompt、`read_file`、`ls`、`transfer_file`、`transfer_path` 一致访问。
- [ ] 用户已有任意来源的同名 Skill 时直接复用本地内容，不访问或覆盖该 Skill，也不要求同源。
- [ ] 任一依赖不可用或安装失败时 Persona 不切换、不增加 usage_count，并满足确定的失败一致性策略。
- [ ] 旧公开 Persona 无需迁移即可在首次使用时完成实体化。
- [ ] 快速连续确认“同步发布并保存”只创建一个 Persona；相同幂等键的重复 POST 返回同一记录。
- [ ] Fast、Search、Team 及现有普通聊天/手动 Marketplace 安装回归通过。
- [ ] 自动安装项永久保留并在用户 Skills 列表中按普通 Marketplace Skill 展示；切换 Persona 后仍存在。

## Out of Scope

- 自动卸载不再被任何 Persona 引用的 Skill。
- 为 Persona 保存独立不可变的 Marketplace Skill 版本快照。
- 校验或覆盖用户已有的同名 Skill。
