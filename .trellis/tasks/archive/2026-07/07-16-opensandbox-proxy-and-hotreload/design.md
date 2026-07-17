# Design — opensandbox proxy 配置与沙盒配置热加载

## 架构与边界

本任务横跨三处代码层，全部为已有结构的增量扩展，不引入新模块：

1. **配置定义层** `src/kernel/config/base.py` + `definitions.py`：新增一个布尔配置项。
2. **沙盒适配器层** `src/infra/sandbox/session_manager.py` + `src/infra/sandbox/base.py`：把新配置透传给 SDK `ConnectionConfigSync`。
3. **配置热刷新层** `src/kernel/config/service.py`：仿 checkpoint 模式，把"沙盒受影响配置变更"接入单例重建。

## 数据流与契约

### proxy 开关数据流（R1）

```
DB system_settings / .env / base 默认
  → settings.OPENSANDBOX_USE_SERVER_PROXY  (bool, default True)
  → OpenSandboxSandboxAdapter.__init__ / _sync_from_settings  读到 _use_server_proxy
  → _get_connection_config()  →  ConnectionConfigSync(domain, api_key, use_server_proxy=...)
  → SandboxSync.create(connection_config=cfg)
```

SDK 契约（`opensandbox==0.1.14`，报错 hint 已明确）：
- `use_server_proxy=True`：SDK 只连 `domain`（opensandbox server），由 server 转发到沙箱内部端口。**跨网络部署必需**。
- `use_server_proxy=False`（默认）：SDK 直连沙箱容器端口。要求调用方与沙箱同网络。

`SandboxFactory.create_opensandbox`（`base.py:214-226`）与 adapter（`session_manager.py:238-241`）是 `ConnectionConfigSync` 的**两个构造点**，必须同步加参，否则经 factory 路径创建的沙箱仍直连。

### 热加载数据流（R2）

复用既有链路（**不新增 pub/sub 通道**）：

```
前端 PUT /settings/{key} → SettingsService.set()
  → refresh_settings(key)                     # service.py:260
      ├─ setattr(settings, key, value)        # 全局 settings 更新（已有）
      ├─ if key in llm_affected_settings      # 已有
      ├─ if key in memory_affected_settings   # 已有
      ├─ if key in _CHECKPOINT_AFFECTED_SETTINGS → _reset_checkpoint_runtime_state()  # 已有
      └─ if key in _SANDBOX_AFFECTED_SETTINGS  → _reset_sandbox_runtime_state()        # 新增
  → _publish_change → Redis SETTINGS_CHANNEL
  → 其它实例 SettingsPubSub._handle_message → refresh_settings(key) → 同上
```

`_reset_sandbox_runtime_state(reason)`：
```python
async def _reset_sandbox_runtime_state(reason: str) -> None:
    try:
        from src.infra.sandbox.session_manager import reset_session_sandbox_manager
        reset_session_sandbox_manager()
        logger.info("[Settings] Sandbox manager rebuilt after %s", reason)
    except Exception as exc:
        logger.warning("[Settings] Failed to rebuild sandbox manager after %s: %s", reason, exc)
```

`reset_session_sandbox_manager()`（新增，放 `session_manager.py` 单例定义旁，~1163）：
```python
def reset_session_sandbox_manager() -> None:
    """置空沙盒单例；下次 get_session_sandbox_manager() 按最新 settings 重建。

    Soft reset：不清空 Mongo user_bindings、不停止运行中沙箱。
    """
    global _session_sandbox_manager
    _session_sandbox_manager = None
```

## 为什么是 soft reset（不 kill 运行中沙箱）

`SessionSandboxManager` 持有：
- `_cache`：进程内 `user_id → (sandbox_id, backend, provider_obj)` LRU。
- Mongo `user_bindings`：持久化的 `user_id → sandbox_id`。

重建单例 → `_cache` 清空（新实例空缓存），但 `user_bindings` **不动**。下次该用户访问时：
- 同平台/同参数变了（如 domain/api_key/image 变）→ `get_sandbox(binding.sandbox_id)` 用新连接配置重连，成功则复用，失败则 fallthrough 新建。
- 平台切换（daytona↔opensandbox）→ 旧 sandbox_id 属于旧 provider，新 adapter 重连失败 → fallthrough 在新平台新建；旧沙箱由 provider 侧 TTL（`DAYTONA_AUTO_DELETE_INTERVAL` / E2B timeout / opensandbox timeout）自然回收。

**收益**：配置保存绝不破坏用户在跑的会话；最坏情况是平台切换后旧沙箱成为短暂孤儿（自愈）。这符合用户确认的 (a) 方案，且与 LLM/memory 热刷新"非破坏性"语义一致。

## 兼容性与迁移

- **默认值 True**：两种已知部署（本机跨网络 / k8s 跨网络）都需要 proxy。默认 True 让新部署开箱可用。若将来出现同网段直连场景，前端开关可切回 False（AC4）。
- **DB 无该 key 时**：走 `base.py` 默认 True（`initialize_settings` 在 DB 无记录时不覆盖，见 service.py:238-247）。
- **既有用户**：升级后 `settings.OPENSANDBOX_USE_SERVER_PROXY=True` 自动生效，无需手动配置。
- **daytona / e2b 用户**：不受影响（新增的 reset 路径对它们同样适用——改 daytona 配置也能热加载，属于附带修复）。

## 关键权衡

| 决策 | 选择 | 理由 |
|---|---|---|
| 配置项 vs 写死 True | 配置项（默认 True） | 用户明确要求；且未来同网段直连场景可切 |
| 默认值 True vs False | **True** | 两种实际部署都跨网络；False 会让新部署默认失败 |
| reset 粒度：整体重建单例 vs 仅换 adapter | **整体重建** | 平台切换必须换 adapter 实例类型，单例重建最简单且覆盖所有子情形 |
| soft vs hard reset | **soft** | 非破坏性，不因配置保存误杀用户沙箱（用户已确认） |
| 新增 pub/sub 通道 | **否** | 复用既有 `SETTINGS_CHANNEL` + `refresh_settings` 钩子 |

## 风险与回滚

- **风险 1**：`_reset_sandbox_runtime_state` 抛异常会否阻断 settings 刷新？→ 不会，try/except 吞掉只 warn（仿 checkpoint），且发生在 `setattr` 之后。
- **风险 2**：单例重建并发——reset 置 None 与并发 `get` 之间有竞态？→ 最坏情况是两个 `SessionSandboxManager()` 被建，后者覆盖；`_get_user_lock` 保证单沙箱操作串行，无数据损坏。可接受。
- **风险 3**：默认 True 后，原本能直连的内网同网段部署被迫走 proxy（多一跳）。→ 可前端切 False；且当前无此部署。
- **回滚**：全部为新增代码 + 默认值；回滚只需 revert 本任务 commits，行为退回现状（需重启）。`OPENSANDBOX_USE_SERVER_PROXY` 在 DB 里的记录回滚后无害（`base.py` 无该字段时 settings 加载忽略未知 key）。

## 涉及文件清单（精确锚点）

| 文件 | 改动 |
|---|---|
| `src/kernel/config/base.py:197-202` | 加 `OPENSANDBOX_USE_SERVER_PROXY: bool = True` |
| `src/kernel/config/definitions.py:495-537` | 加 `OPENSANDBOX_USE_SERVER_PROXY` 定义 |
| `src/infra/sandbox/session_manager.py:214-241` | adapter 加 `_use_server_proxy` 字段 + `_get_connection_config` 透传 |
| `src/infra/sandbox/session_manager.py:1163-1171` | 加 `reset_session_sandbox_manager()` |
| `src/infra/sandbox/base.py:214-226` | `create_opensandbox` 的 `ConnectionConfigSync` 加 `use_server_proxy` |
| `src/kernel/config/service.py:34-57, 287-352` | 加 `_SANDBOX_AFFECTED_SETTINGS` + `_reset_sandbox_runtime_state` + 两处分支接入 |
| `tests/`（新增） | adapter proxy 透传、reset 重建、soft-reset 不清 bindings |
| `frontend/src/i18n/locales/*.json` | `settingDesc.OPENSANDBOX_USE_SERVER_PROXY` 文案（5 语言） |
