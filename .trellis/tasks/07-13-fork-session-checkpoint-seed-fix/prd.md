# 分叉会话继续聊天报错「未知错误」

## 背景

内网 k8s 部署后，对会话分叉、在分叉会话继续聊天时前端返回「未知错误」。日志根因链：

1. 分叉创建时 checkpoint 克隆失败：`Failed to clone fork checkpoints: ... error=Unable to locate fork checkpoint for thread=47736579... turn=1 type=assistant`（`clone_checkpoints_for_fork` 第 455-458 行抛 `ValueError`，`_find_fork_boundary_checkpoint` 找不到边界 checkpoint）。
2. 降级到 `seed_checkpoint_from_messages`（第 480 行），其写入 `checkpoint["channel_versions"] = {"messages": "1"}`（**字符串**）。
3. 分叉会话首条消息触发 LangGraph `get_next_version`（`langgraph/checkpoint/base/__init__.py:706-707`）：对 `str` 类型 version 直接 `raise NotImplementedError`。
4. **MongoDBSaver 没有重写 `get_next_version`**（只有 MemorySaver 重写用 str），所以走 base 默认实现 → 抛 `NotImplementedError` → `str(error)` 为空 → 前端显示「未知错误」。

## Goal

修复分叉会话继续聊天的崩溃，使分叉会话能正常对话；并尽量消除触发 seed 降级的根因（边界 checkpoint 找不到）。

## Requirements

- **P0 修复**：`seed_checkpoint_from_messages` 写入的 `channel_versions` version 类型必须是 LangGraph `get_next_version` 可递增的类型（int），当前是字符串 `"1"` 会导致 `NotImplementedError`。
- **P1 调查**：`_find_fork_boundary_checkpoint` / `_matches_fork_boundary` 为何对 `turn=1 type=assistant` 找不到边界 checkpoint。需用可复现测试定位（猜测：消息类型判定、human_count 计数、或 MongoDB checkpoint 反序列化后的消息结构）。
- 保证修复对 MongoDB backend（内网实际后端）生效，不破坏 Memory/Postgres backend。

## 范围外

- 不改前端「未知错误」文案逻辑（根因在后端空 error，修后端即可）。
- 不重构整个 fork/session 模块，只改 checkpoint seed + boundary 定位逻辑。

## Acceptance Criteria

- [ ] 新增可复现测试：用 MongoDBSaver（或其 mock）构造一个有 `[Human, AI]` 消息的源会话 → 分叉 → 在分叉会话发消息，**不抛 NotImplementedError**，能正常推进。
- [ ] `seed_checkpoint_from_messages` 写入的 `channel_versions` 为 int 版本，`get_next_version` 可正确递增。
- [ ] 调查并记录 `_find_fork_boundary_checkpoint` 找不到 `turn=1 type=assistant` 的真实原因；若属 bug 则修复（使正常路径走 clone 而非 seed 降级）。
- [ ] 非分叉会话回归不受影响（已有正常会话路径测试通过）。
- [ ] `uv run pytest` 相关 checkpoint/fork 测试全部通过。

## Notes

- 文件：`src/infra/storage/checkpoint.py`
  - `seed_checkpoint_from_messages`（第 480-502 行）—— P0 修复点
  - `_find_fork_boundary_checkpoint`（第 400 行）、`_matches_fork_boundary`（第 375 行）、`_extract_checkpoint_messages`（第 368 行）—— P1 调查点
  - `clone_checkpoints_for_fork`（第 435 行）
- LangGraph 契约铁证（已读已安装包源码）：
  ```
  langgraph/checkpoint/base/__init__.py:692
  def get_next_version(self, current, channel):
      if isinstance(current, str): raise NotImplementedError   # 706-707
      elif current is None: return 1
      else: return current + 1
  ```
  MongoDBSaver 未重写此方法（`grep` 确认）；MemorySaver 重写为 str 方案。
- 调用链：`src/infra/session/manager.py` `fork_session_from_message`（第 297 行）→ clone 失败降级到 `seed_checkpoint_from_messages`；`src/infra/task/executor.py` `_handle_generic_error`（第 321 行）吞掉空 str error。
