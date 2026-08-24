# 调研 OpenSandbox 未知节点无法删除与容量不同步

## Goal

让管理员在 OpenSandbox 不可达、托管记录显示 unknown、又几乎看不到日志时，仍能从 UI 同步清掉本地绑定和容量占用（`x/10` 的 x），并切回单节点兼容模式。健康节点继续 fail-closed，防止误删真实占用。

## Background

2026-08-18 多节点任务按 fail-closed 设计：有绑定或 reservation 不得硬删节点；provider 查询失败不得释放槽位。节点可达时能防超配，节点不可达时管理员被锁死。

2026-08-21 生产反馈：多节点特殊情况后托管列表出现 unknown；删除会先调 OpenSandbox；Mongo 记录无法从 UI 删除；容量 x 不回落；因此切不回单节点。

技术依据见 `research/production-orphan-delete-trap.md`。

## Key Decision

强制清理采用方案 A：

- unknown 沙箱行可「仅清理本地账本」：不要求 OpenSandbox 成功，绑定终态化，reservation 释放，`x` 立即下降。
- 节点不可达 / unknown 时可级联强制移除：绑定 + 容量账本 + 节点配置一次完成；若这是最后一个多节点，原子切到 `legacy`。
- 健康且仍有真实占用的节点继续拒绝普通删除。
- 远端容器可能成为孤儿，靠 OpenSandbox TTL 回收；二次确认必须写明这一点。

## Requirements

### R1. 沙箱行本地清理

- 托管列表 `unknown` / `creating`，或所属节点健康为 `unknown` / `unavailable` 时，Admin 可确认后执行「仅清理本地账本」。
- 该动作不得调用 OpenSandbox create/connect/kill；只把对应 binding 标为终态并释放 reservation。
- 现有「终止」仍先调 OpenSandbox：明确 404 走现有对账；超时/连接失败返回可识别错误，并允许随后改走本地清理。
- 默认托管列表不展示已终态行，清理后行消失且该节点 `used_sandboxes` 下降。

### R2. 节点级联强制移除

- 节点健康为 `unknown` / `unavailable`，或仍有本地占用但远端不可达时，Admin 可确认后强制移除该节点。
- 强制移除必须同步：该节点全部非终态 binding 终态化、全部 reservation 释放、删除 `opensandbox_node_capacity` 文档、从节点配置中移除该节点并 bump revision。
- 若移除后多节点列表为空，同一请求把 mode 写成 `legacy`，不得留下非法的空 `multi_node`。
- 普通移除（draft + 保存）仍拒绝有真实占用的健康节点。保存成功移除节点时，即使占用已是 0，也必须删除对应容量文档。

### R3. 切回单节点

- 强制清理后若已无多节点占用，保存 `mode=legacy` 必须成功。
- 最后一个卡住的节点走强制移除时，不要求管理员先手动切模式（否则会被 `opensandbox_legacy_mode_in_use` 挡住）。

### R4. 可观测性

- 列表探测失败、节点探测失败、远端终止失败、本地清理/强制移除，都必须用 `get_logger(__name__)` 写下 `node_id`、`sandbox_id`（若有）、异常类型和是否 local-only。
- 禁止记录 API Key。UI 错误使用稳定错误码，不得只显示 unknown 却没有任何失败原因。

### R5. 权限与确认

- 全部新动作沿用 `settings:manage`。
- 本地清理和强制移除使用危险二次确认，文案说明文件不可恢复、远端容器可能仍在、容量占用会被清零。

## Technical Notes

- 节点健康默认 `unknown`（从未探测）；探测失败才是 `unavailable`。`src/infra/sandbox/node_storage.py:210`，`src/api/routes/settings.py:88-104`。
- 列表探测非 404 异常被吞掉且不打日志，行变成 unknown。`src/api/routes/opensandbox_admin.py:152-185`。
- 终止必须先 connect/kill；非 404 变成 502 `opensandbox_provider_action_failed`，Mongo 不动。`src/infra/sandbox/session_manager.py:307-320`，`src/api/routes/opensandbox_admin.py:252-294`。
- `x` 统计非终态 reservation，缺省/`unknown` 也占槽。`src/infra/sandbox/node_storage.py:116-128`。
- 前端移除节点只改 draft；保存遇占用抛 `opensandbox_node_in_use`，切 legacy 抛 `opensandbox_legacy_mode_in_use`。`OpenSandboxNodesPanel.tsx:338-341`，`node_storage.py:144-182`。
- `save()` 不删除已移除节点的容量文档。`node_storage.py:197-213`。
- `multi_node` 不允许空节点列表。`src/kernel/schemas/opensandbox.py:84-85`。

## Acceptance Criteria

### 调研

- [x] `research/production-orphan-delete-trap.md` 写明 unknown 来源、删除链路、容量账本、切模式阻塞、日志缺口，并带 file:line。
- [x] 明确远端终止 vs 仅清理本地 vs 节点级联强制移除的边界（方案 A）。
- [x] 切回单节点的前置条件：占用清零，或最后一个节点强制移除时原子切 `legacy`；节点删除必须级联删容量文档。

### 实现

- [ ] unknown / 节点不可达时，Admin 可从 UI 清掉对应托管记录，不必 OpenSandbox 调用成功。
- [ ] 沙箱本地清理成功后该节点 `used_sandboxes` 立即下降，行从默认列表消失。
- [ ] 节点强制移除成功后配置中不再有该节点，容量文档删除，`x/10` 不再显示该占用。
- [ ] 强制移除最后一个多节点后 mode 为 `legacy`，可继续使用下方单节点配置。
- [ ] 占用已清但仍停在多节点时，保存 `legacy` 成功。
- [ ] 健康节点上仍有非终态占用时，普通保存移除继续返回 `opensandbox_node_in_use`。
- [ ] 远端明确 404 仍走现有自动对账；超时/宕机不再锁死管理员。
- [ ] 探测/终止/本地清理失败都有后端日志；UI 能看到稳定错误码。
- [ ] 测试：provider 超时/连接失败时本地清理成功；占用释放后可删节点、可切 legacy；健康占用节点仍拒绝普通删除；最后一节点强制移除后 mode=legacy 且容量文档不在。测试不连接真实 OpenSandbox。
- [ ] 后端 ruff/mypy/相关 pytest，前端 eslint/相关测试通过。

## Out of Scope

- 跨节点迁移、自动在其他节点重建沙箱。
- 修改 Daytona / E2B 调度。
- 企业微信渠道的容量提示。
- 发现或管理绕过 LambChat 创建的外部沙箱。
- 对真实 OpenSandbox 做故障演练。
- 在规划批准前改产品代码。
