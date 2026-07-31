# Persona Skill 实体化技术设计

## 1. 设计结论

删除 Persona Skill 的运行时双轨语义：

```text
Persona.marketplace_skills（依赖声明）
        ↓ use_preset
按 skill_name 查询用户 SkillStorage
        ├─ 同名存在 → 直接复用本地 Skill
        └─ 同名不存在 → 从 Marketplace 安装为普通用户 Skill
        ↓
PersonaSnapshot.skill_names
        ↓
Fast / Search / Team 统一从用户 SkillStorage 加载
```

`Persona.marketplace_skills` 继续作为公开 Persona 的持久依赖清单；它不再作为 Agent runtime overlay 的文件来源。

## 2. 核心契约

### 2.1 名称优先

消费者侧只按规范化后的 `skill_name` 判断：

- 用户空间存在任意非 metadata 文件，即认为同名 Skill 已存在；
- 不读取或比较 `installed_from`、`published_marketplace_name`、Marketplace version、文件摘要；
- 本地同名项优先，Marketplace 是否停用或删除不影响本地同名项的使用；
- 仅当本地完全不存在同名项时，才校验并安装 Marketplace 依赖。

创建公开 Persona 时的商城全局同名冲突规则保持不变；“名称优先”只改变消费者使用 Persona 的行为。

### 2.2 永久安装

Persona 自动安装与用户手动 Marketplace 安装写入相同的 SkillStorage：

- 文件写入用户 `skill_files`；
- metadata 写 `installed_from=marketplace`；
- 失效用户 Skill cache；
- 在用户 Skills 列表中正常展示；
- 切换或离开 Persona 不删除。

不新增 `persona_temp`、`mounted`、`auto_installed` 等特殊状态。

### 2.3 禁用偏好

保持当前优先级，不修改用户的 `disabled_skills`：

```text
Persona enabled_skills 白名单 ∩ 用户未禁用 Skills
```

实体化只保证文件存在，不擅自打开用户明确禁用的 Skill。

## 3. 安装领域服务

新增或抽取 `src/infra/skill/installation.py`，承载路由和 Persona 共用的 Marketplace → 用户空间复制逻辑。

建议接口：

```python
async def ensure_marketplace_skill_installed(
    name: str,
    *,
    user_id: str,
    marketplace: MarketplaceStorage,
    storage: SkillStorage,
) -> EnsureSkillInstallResult
```

结果至少区分：

- `reused_local`：名称已存在，未访问/复制 Marketplace；
- `installed`：本次新安装完成；
- `file_count`：新安装文件数。

手动 `POST /api/marketplace/{name}/install` 复用同一底层复制服务，但保持既有 API 契约：发现同名项仍返回 409。Persona 使用方则把同名项视为成功。

## 4. Persona 使用事务边界

`PersonaPresetManager.use_preset` 调整为：

1. 读取可见 Persona。
2. 解析 `marketplace_skills`；旧 Persona 没有 refs 时由 `skill_names` 兼容生成。
3. 第一阶段预检：
   - 本地同名存在：标记复用，不检查商城；
   - 本地不存在：检查 Marketplace 存在、active、包含 `SKILL.md`。
4. 第二阶段安装所有缺失项。
5. 全部成功后：
   - snapshot 只返回 `skill_names`；
   - `marketplace_skills` runtime 字段置空/停止下游传播；
   - 增加 Persona usage_count 并记录偏好。
6. 失败时：
   - 不增加 usage_count；
   - 不切换 Persona；
   - 清理本次调用新建但未完成的 Skill 文件和 metadata；
   - 用户原有同名项永不修改。

先预检再写入可避免“后一个依赖不可用”造成的常规半安装。复制过程异常仍使用补偿清理；不引入新的分布式事务系统。

并发首次使用依靠现有 `(skill_name, user_id, file_path)` MongoDB 唯一索引和幂等 upsert 收敛到同一份内容。补偿只能清理本次明确创建且尚未形成有效 metadata 的项，避免删除已经完成的并发安装。

## 5. 移除 Overlay 双轨

完成实体化后：

- `resolve_persona_request` 不再把 `snapshot.marketplace_skills` 写入 `agent_options`;
- Fast、Search、Team context/backend 不再需要 Persona Marketplace refs；
- `SkillsStoreBackend` 始终使用普通 `SkillStorage`；
- 删除或退役 `PersonaSkillStorageOverlay`、`load_persona_marketplace_skills` 及其专用测试；
- 保留 Persona schema 中的持久依赖字段，但明确它不是 runtime 文件后端。

清理按调用链自上而下进行，避免留下“字段仍在传递但永远为空”的假兼容层。

## 6. Persona 创建幂等

### 前端

- 新建编辑器会话生成稳定的 `create_request_id`；
- 预检、弹框确认和最终创建共用该 ID；
- 使用同步 `useRef` 作为提交锁，事件重入时立即返回；
- React loading state 仅负责展示，不再承担唯一防重职责。

### 后端

- `PersonaPresetCreate` 接受可选 `create_request_id`；
- `persona_presets` 建立 `(created_by, create_request_id)` partial unique index；
- 相同创建人和 request ID 的重复 POST 返回首次创建的 Persona；
- 不使用 Persona 名称做唯一键；
- 旧客户端不传 request ID 时保持原行为。

已存在的重复 Persona 不自动删除，避免误删合法同名记录。

## 7. 兼容性

- 旧公开 Persona：首次使用时按现有 `marketplace_skills` 或回退 `skill_names` 实体化。
- 已安装同名 Skill：无论来源直接复用，不迁移 metadata。
- 旧会话：下一次服务端解析 Persona 时重新生成 snapshot，不依赖旧客户端提交的 overlay refs。
- 手动商城安装、更新、用户手工 Skill 行为保持不变。

## 8. 回滚

- 可先恢复 `use_preset` 的 overlay snapshot 输出和 `resolve_persona_request` 传播；
- 新安装到用户空间的 Skill 是合法普通 Marketplace Skill，回滚时无需删除；
- 创建幂等字段和索引可保留，对旧请求无影响。

