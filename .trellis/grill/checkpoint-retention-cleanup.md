# Grill: Checkpoint 定期清理（保留期回收）

Date: 2026-09-17
Status: 已确认 A 方案，已实现
Target project: github.com/123zhaozheng/kunxiaozhi（本 thread 仓库为 Yanw，非目标仓库）

## Intent

生产 LangGraph checkpoint 无限增长。目标：最优雅、最小侵入的周期清理，
且满足：历史可查看 / 久远会话继续聊不报错（可丢上下文）/ 统计不受影响。

## Verified findings（代码证据）

### 1. 历史展示与 checkpoint 无关 → 可清理
- `session.py:277-350` → `dual_writer.py:923-1013`，只读 TraceStorage / Redis。
- `dual_writer.py` / `trace_storage.py` / `storage.py` 中 `checkpoint` 零匹配。

### 2. 继续聊天不报错，仅丢模型侧上下文
- 外层 graph 无状态：`fast_agent/graph.py:111-114`、`search_agent/graph.py:121-124`
  以 `checkpointer=None` 编译；上下文在内层 deep agent。
- 内层每轮只传新消息：`fast_agent/nodes.py:311-315`、`search_agent/nodes.py:346-347`。
- `langgraph/pregel/_loop.py:1888-1894`：`aget_tuple` 返回 `None` 时退化为
  `empty_checkpoint()`，**不抛异常** → HTTP 200。
- 正常聊天路径**无**从 trace 重建逻辑：`seed_checkpoint_from_messages` 仅
  `manager.py:420`（fork 流）调用。

### 3. 统计零影响（决定性）
- `src/infra/analytics/` 与 `routes/analytics.py` 中 `checkpoint` **零匹配**。
- 数据源全集（`analytics/storage.py:159-238`）：`traces`、`sessions`、`users`、
  `feedback`、`persona_presets`、`analytics_daily_snapshot`、`user_daily_activity`。
- Token 反规范化写入 trace 事件：`trace_storage.py:699-742` `$push token:usage`；
  读取 `usage_query.py:83`、`storage.py:459`。
- 历史值已冻结：`snapshot.py:216-283` 完成标记后永不重算；`_freeze_dates`
  以 `$setOnInsert` 写入不可变行（`snapshot.py:507-524`）。
  → 即使原始数据消失，已冻结的历史统计仍然存活。
- 重跑 backfill 需要 traces/sessions（`backfill.py:253-278,340+`），**不需要 checkpoint**。

### 4. 其他读取方与风险
- `task/recovery.py`：走正常 stream 路径，行为同第 2 点。
- `team_agent/sop/store.py:1-4`：自带 `sop_runs` 集合，独立于 message checkpointer。
- 人工审批：全仓无 `interrupt()` / `NodeInterrupt` / `GraphInterrupt`；
  审批态在 `approvals` 集合 + Redis pubsub（`human_tool/tool.py:110-172` 进程内 await）。
  → "pending interrupt 被清掉"这一担忧不成立；审批超时 ≤300s，月级窗口撞不上。
- 用户级"消息检查点"（`manager.py:288-320`）存在 `session.metadata.checkpoints`
  （sessions 集合），**不是** LangGraph 集合；但"从检查点分叉"依赖 LangGraph
  checkpoint，清理后降级为从 trace 文本重建（`manager.py:404-420`），仍可用。

### 5. 增长原因 + 现状无过期机制
- `MongoDBSaver` 支持 `ttl=`（`saver.py:117-151`），但项目未传
  （`storage/checkpoint.py:159-163`），故 `put()` 也不写 `created_at`
  （`saver.py:423-425`）。
- 两个集合：`checkpoints` + `checkpoint_writes`（默认名，未覆盖）。
- 唯一删除路径 `delete_checkpoints_for_thread`（`checkpoint.py:508-524`），
  仅整会话删除时由 `manager.py:245` 调用，无定时任务。

## Key decisions

- 决策：**不用 `MongoDBSaver(ttl=...)` 原生 TTL。**
  原因：`deepagents 0.6.7` 的 messages 通道为
  `DeltaChannel(snapshot_frequency=50)`（`deepagents/graph.py:63-66`），
  状态靠祖先链回放重建（`langgraph/pregel/_checkpoint.py:136-185`
  `channels_from_checkpoint` → `get_delta_channel_history`）。TTL 按单文档过期
  会打断祖先链 → 半损坏状态（seed 丢失/部分回放），而非干净空会话；
  且存量文档无 `created_at`，索引对历史数据不生效。
  → 必须**按 thread/session 整体删**（`delete_thread` 同清两个集合），
  保证要么全在要么全无。
  备选（已否决）：一行 `ttl=` 配置——表面最优雅，实则有损坏风险。

- 决策：活跃度依据 **`sessions.updated_at`**，非 checkpoint 自身时间。
  原因：checkpoint 无时间字段；`updated_at` 每次对话写入即刷新
  （`dual_writer.py:156,527`），且已有索引 `user_status_updated_idx`
  （`storage.py:91`）。既有 cutoff 查询先例：`storage.py:666-694`。

- 决策：复用 **FastAPI lifespan 后台 worker + Redis NX 分布式锁**范式
  （`analytics/daily_freeze.py`；注册见 `main.py:87-96` 任务名登记、
  `main.py:568-582` 启动、`main.py:336-338` 关停）。
  原因：全仓**无 arq cron 先例**（`arq_worker.py:147-148` 无 `cron_jobs`，
  `cron_jobs` 全仓零匹配）；多副本下 NX 锁是本仓既有解法。
  备选（已否决）：arq cron（新机制无先例）、k8s CronJob（脱离配置体系）。

- 决策：只删 LangGraph checkpoint，不碰 traces/sessions/统计集合。

## 实现落点（已确认）

- 新 worker：`src/infra/checkpoint/cleanup_worker.py`，锁 key 如
  `checkpoint:cleanup:lock`，结构照抄 `run_once`/`_renew_lock_loop`/
  `run_forever`/`close`；可用"本月已执行"标记文档（仿 snapshot 完成标记）。
- 注册：`main.py` `_LIFESPAN_BACKGROUND_TASK_NAMES` + `asyncio.create_task`。
- 配置：`src/kernel/config/_definitions_extra.py` 按既有 dict schema 增加
  `CHECKPOINT_CLEANUP_*`（`type`/`category`/`subcategory`/`description`/
  `default`/`depends_on`...，见 `schemas/setting.py:18-106`）。
- i18n：`settingDesc.<KEY>` 需同步 **5 个** locale 文件
  （`frontend/src/i18n/locales/{en,ja,ko,ru,zh}.json`，约 :2187 的
  `settingDesc` 块内按字母序）。

## Resolved decisions

1. 采用 **A**：按 `sessions.updated_at` 超过保留期未活跃逐会话清理。
2. 天数/频率/批量全部做成可配置项（默认 30 天 / 24 小时 / 200 条），
   并设 7 天下限与 1000 条上限，避免误配置造成伤害。
3. 默认 `CHECKPOINT_CLEANUP_ENABLED=False`，需管理员显式开启（可逆）。
4. 额外防御：跳过存在 pending 审批的会话；审批检查异常时 fail-closed 跳过。

## Out of scope

- 清理 traces / 会话历史本身（破坏历史展示与未冻结日统计）。
- Postgres checkpoint 后端清理（需确认生产是否启用）。
- 修改 deepagents/langgraph 的 delta 通道行为。
