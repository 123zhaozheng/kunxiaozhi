# Persona Harness Skill 与内置只读 Skill 重构

## Goal

重新划清 Persona、Marketplace Skill、Builtin Skill 与 Agent Harness 的职责：Persona 只注入专家提示词与精确 Skill 名称，由 Search Agent 在沙箱内主动调用 `install_skill(name)`；Builtin Skill 则作为系统管理、用户可见但只读的个人空间 Skill。

## Background / Confirmed Facts

- `install_skill` 已支持按 Marketplace 唯一名称直接下载到当前沙箱的 `temp_skills/{name}`，并返回 `SKILL.md` 路径；它不写用户 MongoDB `skill_files`。
- `find_skills` 仅用于名称未知时的搜索。Persona 已保存精确名称，因此 Persona Harness 不需要先调用 `find_skills`。
- `install_skill` 只有启用 Sandbox 时才注册；Persona Skill 能力必须绑定 Search Agent 与可用沙箱。
- 当前 Persona schema 允许 `fast | search | team`，编辑器也允许三者；后端使用 Persona 的 preferred agent 强制锁定会话。
- 当前 Persona `skill_names` 会被解析成会话 `enabled_skills`；今天新增的逻辑还会在 `use_preset` 时把 Marketplace Skill 永久复制到用户个人空间，这部分需要回撤。
- 当前 Persona 公开发布流程会把选中的个人 Skill 同步发布到 Marketplace，并持久化精确 Marketplace 名称；该名称可以直接作为 `install_skill` 参数。
- Builtin Skill 当前使用独立 collection，并在 `get_effective_skills()` 中按角色合并；这不是“用户个人空间可见只读”的最终模型。
- Persona 编辑器表面上以 `editingPreset` 决定 PUT 或 POST，后端也支持 user→global 原地更新；但旧的 Persona 内 Skill 发布确认编排使“个人 Persona → 官方已发布”出现保留个人记录并新增官方记录的异常。新架构删除整条发布编排，并用一次 PUT、原 id 不变的回归测试锁定正确语义，不能再用创建幂等掩盖。

## Requirements

1. Persona 继续允许选择精确 Marketplace Skill 名称，但不再把 Skill 文件注入用户空间，也不再通过 `/skills` overlay 挂载。
2. Persona 带 Skill 时必须使用 Search Agent；Team Agent 对所有 Persona 强制禁用，前后端均需校验，不能只隐藏 UI。
3. Harness 根据 Persona snapshot 中的精确 Skill 名称与最新 description 稳定注入候选能力；只有当前任务命中某项时才直接调用 `install_skill(name)`，不得为这些已知名称先调用 `find_skills`。
4. Persona Skill 安装继续落在当前沙箱 `temp_skills`，不污染用户持久 Skill 空间；同一沙箱重复调用保持幂等。
5. 精确回撤今天新增的 Persona 使用时永久安装、安装领域服务抽取及相应双轨代码，保留无关并行工作。
6. 删除今天新增的 `create_request_id` 前端字段、提交锁、后端 schema、Mongo 唯一索引与原子 upsert；它不是本次重复记录的正确修复。
7. 修复 Persona 转官方发布语义：编辑现有个人 Persona 时直接 PUT 原 Persona id，将同一记录改为 global/public/published；不得 POST、copy 或保留旧个人记录，也不得触发任何 Skill 发布确认或发布请求。
8. Builtin Skill 不进入 Marketplace；系统把它作为用户个人 Skill 展示，用户可读取、启用/禁用和使用，但不得编辑、删除、覆盖或重新发布。
9. Builtin 只读权限必须在所有后端写入口统一执行，包括 API 与 Agent `/skills` 的 write/edit/delete/upload，不能只靠前端按钮。
10. Builtin 的系统更新必须通过逻辑投影立即反映到用户可见视图，不创建用户副本，同时不影响普通手工或 Marketplace Skill。
11. 兼容已有 Persona 数据与已有用户 Skill，迁移过程不得误删用户内容。
12. Builtin 与用户 Skill 同名时，用户 Skill 永远优先：不注入、不覆盖、不改 metadata、不强迫切换；运行时、Skills 列表与 `/skills` 都必须解析到用户版本。
13. 管理员更新 Builtin 时，所有实际使用该 Builtin 的用户应看到新版本；被用户同名 Skill 遮蔽的用户继续使用自己的内容，不接受 Builtin 更新覆盖。
14. Builtin 采用逻辑投影而非物理复制：底层继续只存一份 `skill_builtin`/`skill_builtin_files`，用户 Skills 列表与 `/skills` 运行时按角色动态合并为“个人空间中的系统只读 Skill”。
15. Builtin 管理员更新单一源记录后，未被用户同名 Skill 遮蔽的运行时和列表直接读取新内容，不扫描用户、不生成同步副本。
16. Persona Harness 不在会话开始时立即安装全部 Skill；它稳定注入每个绑定 Skill 的精确 Marketplace 名称和 `SKILL.md` frontmatter `description`，供模型判断任务是否需要该能力。
17. 当用户任务匹配某个绑定 Skill 的 description 时，Search Agent 直接调用 `install_skill(exact_name)`，不得先调用 `find_skills`。
18. `install_skill` 保持现有接口、沙箱目录、幂等与返回值设计；Persona Harness 不规定安装后的 `read_file`、`ls` 或脚本执行顺序，由 Agent 根据工具返回和任务自行操作。
19. Persona 只持久化 Marketplace Skill 唯一名称，不保存 description 快照；每次激活 Persona 时按名称批量确认 active Marketplace 元数据，并批量读取当前 `SKILL.md`、解析 frontmatter `description`。Skill 文件更新后无需重存 Persona 即可生效。
20. Persona 绑定 Skill 后来缺失、停用或无法取得有效 description 时静默降级：不向 Harness 注入该 Skill，不阻断 Persona 激活与聊天，普通用户 UI 不弹错误；可保留内部日志或缺失名称供管理排查。
21. 系统尚未上线，不为历史 `preferred_agent_id="team"` Persona 编写迁移或兼容逻辑；新前端移除 Team，新创建/更新 API 从现在起拒绝 Persona 使用 Team。
22. 一个 Persona 可绑定多个精确 Marketplace Skill；Harness 注入每个可用 Skill 的名称与最新 description，Search Agent 仅在任务匹配时安装所需项，不预装全部。
23. Builtin 内容与版本归系统管理；用户可以禁用、收藏、置顶 Builtin，这些只是用户偏好，不构成内容修改。用户不得编辑、删除、重命名、覆盖、上传文件或发布 Builtin。
24. 管理员更新 Builtin 不重置用户的禁用、收藏、置顶状态；用户偏好继续按 Skill 名称保留。
25. 全局 Sandbox 或 `install_skill` 不可用时，带 Skill 的 Persona 静默降级：继续使用 Search Agent 和 Persona 提示词，不注入 Skill 安装 Harness，普通用户 UI 不报错，仅记录内部日志。
26. Persona 编辑器只允许从 Marketplace 选择 active Skill，不再选择用户个人 Skill；Persona 保存的每个名称必须是可查询的 Marketplace 精确唯一名称。
27. 删除 Persona 内“个人 Skill 发布预检 → 确认发布 → 保存 Persona”的弹窗、API 编排、补偿逻辑与专用字段。Skill 发布只能在 Skills 管理/商城流程中完成，Persona 只组合已发布能力。
28. 编辑个人 Persona 为官方 Persona 始终原地更新同一记录；由于不再存在 Persona 内 Skill 发布确认弹窗，该转换直接走一次 update 请求。

29. Persona 编辑器完全移除 Team Agent 选项；未选择 Skill 时仅可选择 Fast 或 Search。选中任意 Skill 后，前端自动切换到 Search 并禁用 Fast；移除全部 Skill 后重新开放 Fast，但不自动切回。后端拒绝所有 Team Persona，并拒绝任何带 Skill 但不是 Search 的 Persona。

30. Builtin Skill 与同名用户 Skill 使用彼此隔离的偏好身份。用户 Skill 遮蔽 Builtin 时不继承 Builtin 的禁用、收藏、置顶状态；Builtin 偏好在遮蔽期间保留，用户 Skill 删除后 Builtin 重新出现并恢复原有偏好。
31. Persona 保存时由后端批量验证所有 `skill_names` 都对应 active Marketplace Skill；任一名称无效则拒绝保存，但 Persona 使用阶段遇到后来缺失或停用的名称只静默省略。
32. Persona 绑定的 Marketplace 名称仅用于 Search Agent Harness，不再写入或覆盖会话 `enabled_skills`；用户原有 Skill 空间继续按自身启用/禁用状态正常工作。
33. admin 更新 Builtin 的元数据或文件内容后只修改系统单一源并失效相关投影缓存；用户无需同步或复制即可读取新版本，被同名用户 Skill 遮蔽的用户不受影响。

## Acceptance Criteria

- [ ] 带一个或多个精确 Skill 名称的 Persona 只能保存为 Search Agent，并在会话 Harness 中出现直接 `install_skill` 指令。
- [ ] Persona Harness 不要求调用 `find_skills`，且只有工具实际可用时才注入可执行指令。
- [ ] Persona Harness 对每个绑定 Skill 注入精确名称与 description，不在普通问候等无关任务中预装全部 Skill。
- [ ] 多 Skill Persona 可分别按 description 触发对应的精确 `install_skill(name)`，不会强制安装未命中的其他项。
- [ ] 现有 `install_skill` 行为与测试保持不变，Persona 仅新增调用提示，不重写安装工具工作流。
- [ ] Marketplace Skill 的 `SKILL.md` frontmatter description 更新后，再次激活关联 Persona 的 Harness 使用新描述，Persona 记录本身无需迁移或更新。
- [ ] Marketplace Skill 缺失/停用/无有效 description 时，Persona 正常进入聊天，Harness 不出现该 Skill，UI 无错误 toast 或阻断弹窗。
- [ ] Persona 启用不写用户 `skill_files`；Skill 只安装到当前沙箱 `temp_skills`。
- [ ] Persona 无论是否带 Skill 都不能选择或运行 Team Agent；伪造 API 请求被后端拒绝。
- [ ] 今天的 Persona 永久实体化代码被精确删除，Marketplace 手动安装 API 行为不回归。
- [ ] `create_request_id` 及其前后端幂等实现被删除。
- [ ] 个人 Persona 编辑为官方已发布后，MongoDB 仍只有原 id 一条记录，scope 原地变为 global；前端只调用一次 update API，且没有 Skill 预检、确认或发布请求。
- [ ] Persona Skill 选择器只展示 active Marketplace Skills；未发布的个人 Skill 不出现，Persona 页面没有 Skill 发布预检/确认弹窗。
- [ ] 个人 Persona 转官方只发一次 PUT 并保留原 id，不再触发任何 Skill 发布请求。
- [ ] Builtin Skill 出现在用户 Skills 列表，来源明确，用户写入/编辑/删除/发布均被后端拒绝。
- [ ] Agent 能读取并执行 Builtin Skill，但无法通过 `/skills` 修改它。
- [ ] Builtin 更新能覆盖系统管理内容，普通用户同名 Skill 不被静默覆盖。
- [ ] 用户已有同名 Skill 时，Builtin 不产生第二个同名列表项，所有读取与执行均命中用户版本。
- [ ] 管理员更新 Builtin 后，未被同名用户 Skill 遮蔽的用户立即或按既定同步点读到新内容；被遮蔽用户的数据和行为不变。
- [ ] MongoDB 不为每个用户复制 Builtin 文件；用户列表、prompt、read、ls、grep、glob、transfer 对同一解析结果保持一致。
- [ ] 用户可禁用、收藏、置顶 Builtin；管理员更新后这些偏好不丢失，同时所有内容写操作仍由后端拒绝。
- [ ] Sandbox/`install_skill` 不可用时，Persona 正常聊天且 UI 无错误；系统提示中不出现无法执行的安装指令。
- [ ] Fast、Search、普通商城安装、Persona、Builtin 及 transfer 相关回归通过。
- [ ] Persona 编辑器中不存在 Team 选项；选中 Skill 后立即切到 Search 且 Fast 不可选，移除全部 Skill 后 Fast 恢复可选；伪造请求仍会被后端拒绝。
- [ ] 同名用户 Skill 与 Builtin Skill 的禁用、收藏、置顶状态互不污染；遮蔽解除后 Builtin 恢复此前偏好。
- [ ] Persona 保存会拒绝缺失或 inactive 的 Marketplace Skill 名称；保存后 Skill 再失效时，Persona 聊天静默降级且 UI 不报错。
- [ ] 激活 Persona 不修改 `enabled_skills`，用户个人/Builtin Skill 的正常可见性不被 Persona 绑定名单覆盖。
- [ ] admin 原地替换 Builtin 文件后，未被遮蔽的用户通过列表、prompt、read 与 transfer 读取到新内容，MongoDB 不产生用户副本。

## Out of Scope

- 让 Persona 自动搜索名称未知的 Skill。
- 把 Persona Skill 永久安装到用户个人 Skill 空间。
- 允许 Persona 使用 Team Agent。
- 历史 Team Persona 数据迁移、回填或兼容脚本。

## Open Product Decisions

- 无。
