# OpenSandbox 多节点调度与管理

## Goal

让管理员在 Kunxiaozhi/LambChat 设置页面配置多个独立部署的 OpenSandbox 节点及各节点容量，并让多个无状态后端副本安全地为用户选择节点、创建和复用沙盒，同时保持现有 OpenSandbox 生命周期语义和单节点部署兼容。

## Background

- 当前 OpenSandbox 配置只有一个 endpoint；沙盒绑定以 `user_id -> sandbox_id` 持久化，进程内缓存和锁不能协调多个后端副本。
- OpenSandbox `sandbox_id` 属于节点本地身份；跨节点路由必须同时保存 `node_id`。
- OpenSandbox SDK 提供节点内创建、连接、续期、暂停、终止、列表和指标，但不提供跨服务端迁移容器文件系统的能力。
- 技术依据记录在任务的 `research/` 目录，包括多节点可行性、实现映射、生命周期、pause 语义和容量不足 UX 边界。

## Requirements

### R1. Admin 节点配置

- 提供 OpenSandbox 专用节点配置 API 和设置面板，支持节点 ID、domain、API Key、image、timeout、work dir、server proxy、最大沙盒数、启用状态和优先级。
- 节点 ID 创建后不可变；节点列表整体保存必须带 revision，过期 revision 不得覆盖新配置。
- API Key 必须字段级加密保存，响应只返回 `has_api_key`；空值保留旧密钥，显式 clear 才删除。
- 禁用节点只阻止新分配；已有绑定仍可连接、续期和停止。存在绑定或 reservation 时不得硬删除节点。

### R2. 兼容与启用边界

- 多节点模式未启用或没有专用配置时，继续使用现有 OpenSandbox scalar 设置和 `legacy-default` 节点，行为不变。
- 旧绑定只有 `sandbox_id` 时，在受控迁移路径中定位所属节点并回填 `node_id`；节点不可达时不得静默创建替代沙盒。
- 多节点配置变更复用现有跨副本设置通知/软重置机制，不停止运行中的沙盒或删除绑定。

### R3. 无状态容量调度

- 多个后端副本必须通过共享 Mongo 容量账本原子预留节点槽位，不能使用“读取 provider 列表后计数再创建”的竞态流程。
- 同一用户的新建临界区必须使用可续期的 Redis token lease，并通过 Mongo `allocation_token` fencing 防止过期持有者覆盖新绑定。
- 节点选择按 `reservations / max_sandboxes` 最低优先，并使用 priority 和稳定顺序打破平局。
- creating、running、paused、stopping 和 unknown 等所有非终态 reservation 都占用容量；只有确认终止或不存在后才能释放。
- 当所有可选节点容量已满时，需要沙盒且当前没有可复用绑定的用户本次请求立即返回稳定、可识别的容量满错误，不创建沙盒或发送 Agent 消息。

### R4. 用户绑定与生命周期

- OpenSandbox 绑定持久化 `provider + node_id + sandbox_id + reservation_id + allocation state/token`。
- 已绑定用户始终路由到记录节点；缓存命中、跨 Session 重连、renew、显式 stop 的 pause-first/fallback-kill 语义保持不变。
- 节点暂时或永久不可用时采用 fail-closed：保留绑定和 reservation，返回明确的暂不可用错误，不自动跨节点重建。
- 新沙盒创建失败必须幂等补偿 reservation；创建成功但绑定最终写入失败时必须尽力终止孤儿沙盒并保守处理槽位。
- 节点默认 TTL 保持 `3600` 秒并可单独配置；成功使用或 Admin renew 将到期时间刷新为 `now + node.timeout`。

### R5. 对账和可观测性

- 对过期 creating reservation、容量满节点和管理员 probe 提供保守对账；provider 查询失败不能作为释放槽位的依据。
- Admin 显示每节点 enabled/health、已占用/最大容量、最近成功时间和最近错误，不返回或记录密钥。
- 节点探测、容量不足、节点不可用、revision 冲突和 drain/remove 冲突必须有稳定且可本地化的错误结果。

### R6. 单沙盒 Admin 列表与操作

- 同一版本提供单沙盒管理列表，每行展示所属节点、关联用户、sandbox ID、provider 状态、容量状态、创建时间、最后使用时间和绝对到期时间。
- 列表仅展示由 LambChat 创建且存在 binding/reservation 的 managed 沙盒；本版本不发现或展示绕过 LambChat 创建的外部沙盒。
- 行尾根据当前状态显示可执行按钮：pause、resume、renew TTL 和 terminate；不适用的动作必须禁用或隐藏。
- Admin 页面可见时每 10 秒刷新节点和沙盒状态，保留手动刷新按钮；生命周期动作成功后立即刷新对应行。
- 所有动作必须通过 LambChat 管理服务执行并同步 binding、reservation 和 provider 状态；不得由浏览器直接调用 OpenSandbox。
- terminate 必须二次确认并明确提示容器文件不可恢复；pause 不冻结 TTL，renew 必须显示新的绝对到期时间。
- Docker pause 在 UI 中称为“冻结”：不释放内存、不自动 renew，用户下次使用时自动 resume，Admin 也可手动恢复。
- terminate 成功后 binding 标记为 terminal、reservation 释放，用户下次提问可创建新的空沙盒。
- 不增加用户任务 busy 检测；Admin 生命周期动作直接执行，并提示可能中断正在进行的任务。

### R7. 用户容量不足反馈

- Web Chat 用户使用会申请沙盒的 Agent，且没有健康的现有绑定、当前也无法立即分配容量时，本次消息不进入对话，输入草稿保持不变，并弹出“沙盒资源暂满，请稍后重试”提示。
- 弹窗提供可点击的 `?` 帮助入口，内容提示“如需协助，可反馈数据资产部赵正通”。
- 系统不保存等待项、不保存原请求、不自动恢复执行；后续容量可用时由用户自行再次发送。
- 不需要沙盒的 Agent 不受容量检查和弹窗影响。
- 正常首次创建或复用沙盒时不显示额外弹窗，沿用现有加载体验；仅在容量不足或没有可分配节点时打断用户。
- 用户侧将容量满、绑定节点不可达和暂时无法分配统一展示为“沙盒资源暂满，请稍后重试”；内部原因仅在 Admin 状态和后端日志中区分。

## Acceptance Criteria

- [ ] 两个容量为 1 的健康节点可分别接纳两个用户；第三个新用户收到明确的容量不足错误，任何节点都不超配。
- [ ] 两个后端副本并发请求同一新用户时，provider 最多创建一个沙盒，最终只有一个有效绑定和一个 reservation。
- [ ] 后端重启或请求落到另一副本后，用户仍按 `(node_id, sandbox_id)` 回到原沙盒并续期。
- [ ] 绑定节点不可达时不覆盖绑定、不释放槽位、不在其他节点自动创建；用户侧显示统一的资源暂满提示，Admin/日志保留真实原因。
- [ ] provider 创建失败、binding CAS 失败、lease 丢失和进程中断路径均有幂等补偿或保守对账测试。
- [ ] pause 保留 reservation；确认 kill/TTL 终态或 provider 明确不存在后释放 reservation。
- [ ] 禁用或降低节点容量不会停止已有沙盒；节点不再接收新分配，并正确显示 draining/over-capacity。
- [ ] 无专用多节点配置时，现有单节点配置、绑定、缓存、renew、stop 和热更新测试保持通过。
- [ ] 节点 API Key 在 Mongo 中加密，GET/错误/日志/前端状态中不出现明文，revision 冲突返回 HTTP 409。
- [ ] Admin 可新增、编辑、探测、禁用和受约束删除节点，并显示健康状态与容量占用；移动端和桌面端文本不溢出。
- [ ] Admin 可分页查看单沙盒状态和关联用户，并在行尾执行状态适用的 pause、resume、renew TTL、terminate；动作完成后 provider、binding、reservation 和列表状态一致。
- [ ] 所有节点满载时，需要新沙盒的本次消息不发送、不创建排队项，输入草稿保留；用户稍后手动重试。
- [ ] 仅需要沙盒的 Agent 在容量不足时展示提示弹窗和 `?` 帮助内容；无需沙盒的 Agent 不受影响。
- [ ] 沙盒准入失败发生在 run/session/user-message 持久化和 SSE 输出之前；前端移除临时乐观消息并保留文本与附件草稿。
- [ ] Admin 列表仅在页面可见时每 10 秒刷新，支持手动刷新，生命周期动作完成后立即更新对应行。
- [ ] 后端单元/竞态/API 测试、ruff、mypy，以及前端测试、eslint、build 全部通过；测试不连接真实 OpenSandbox。

## Out of Scope

- 跨 OpenSandbox 节点迁移、自动故障重建、快照复制或共享文件系统方案。
- 集群 CPU/内存 Prometheus 仪表盘和跨节点聚合资源告警。
- 沙盒容量等待队列、队列位置、后台自动重放用户请求。
- 用户任务 busy 检测、Admin 操作延迟或强制中断模式。
- 修改 Daytona/E2B 的调度或生命周期语义。
- 企业微信渠道的 OpenSandbox 预持久化准入、容量反馈和发送阻断。
- 发现或展示绕过 LambChat 创建的外部 OpenSandbox 沙盒。
- 对真实 OpenSandbox 节点执行创建、压测、pause、kill 或故障演练。
