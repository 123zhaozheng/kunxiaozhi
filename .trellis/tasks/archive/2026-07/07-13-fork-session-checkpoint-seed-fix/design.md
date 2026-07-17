# Design — 分叉会话 checkpoint seed 修复

## 根因（已坐实）

`langgraph/checkpoint/base/__init__.py:692` 默认 `get_next_version`：

```python
def get_next_version(self, current, channel):
    if isinstance(current, str): raise NotImplementedError   # ← 命中
    elif current is None: return 1
    else: return current + 1
```

- `MongoDBSaver` **未重写**此方法（grep 已确认 `.venv/.../checkpoint/mongodb/__init__.py` 无 `get_next_version`）→ 走 base 默认。
- `MemorySaver` 重写为 str 方案（`checkpoint/memory/__init__.py:619`），所以内存后端不崩——掩盖了 bug。
- `seed_checkpoint_from_messages`（`checkpoint.py:492`）写 `channel_versions = {"messages": "1"}`（str）→ 分叉首条消息 `apply_writes` 调 `get_next_version("1", ...)` → `NotImplementedError` → 空 str error → 前端「未知错误」。

## P0 修复：seed 的 version 改为 int

`checkpoint.py:492`：

```python
# 改前
checkpoint["channel_versions"] = {"messages": "1"}
# 改后
checkpoint["channel_versions"] = {"messages": 1}
```

依据 base `get_next_version`：`current=1`(int) → `return current + 1 = 2`，可正常递增。`current=None` 会返回 1，但这里显式设 1 表示「messages channel 已写入这批 seed 消息、位于 version 1」，语义正确。

`checkpoint.py:494` `updated_channels = ["messages"]` 保留。`checkpoint.py:493` `versions_seen = {}`：seed 时不预设消费者节点已读版本，由后续 Pregel 循环自行推进——保持现状（如测试发现需补充某个节点的 seen 版本，再在测试驱动下补）。

> 备选方案：直接不设 `channel_versions`（用 `empty_checkpoint()` 默认的 `{}`），则 `current=None → return 1`。也可行，但显式设 int 1 更清晰地表达「这批消息已落 version 1」。采用显式 int 方案。

## P1 调查：boundary checkpoint 找不到

`_matches_fork_boundary`（`checkpoint.py:375`）对 `turn=1 type=assistant` 要求：`human_count == 1` 且 `messages[-1]` 是 AIMessage。

**待复现验证的怀疑点**（subagent 用测试逐一排除）：

1. **消息类型判定**：`_is_ai_message` 用 `type(message).__name__ == "AIMessage"`。若 MongoDB 反序列化后的消息是 `BaseMessage` 子类但 `__name__` 不同（或带修饰器），判定失败。需打印实际类型名。
2. **human_count 计数**：若源会话在分叉点前的 checkpoint 累积了多条 Human（如系统注入的引导消息），`human_count != 1` 不匹配。
3. **checkpoint 选取**：`alist` 分页扫描，`_matches_fork_boundary` 对每个 checkpoint 判断「恰好 human_count==turn 且末尾是对应类型」。若边界对应的 checkpoint 其 `channel_values["messages"]` 暂未含 AI 回复（assistant 回复落在下一个 checkpoint），则永远匹配不到 assistant 类型边界。
4. **checkpoint_ns / 线程隔离**：分页 `alist` 的 `before` 游标推进是否正确覆盖目标 checkpoint。

**调查方法**：写测试，构造源会话发一条消息得到 AI 回复（产生真实 checkpoint 序列），调用 `_find_fork_boundary_checkpoint(turn=1, type="assistant")`，打印每个扫描到的 checkpoint 的 `messages` 类型与 human_count，定位失败原因。按真实原因修 `_matches_fork_boundary` 或调用方 turn 语义。

> 若调查后判定 boundary 逻辑无 bug、属于特定数据形态（如该源会话 checkpoint 本就缺失），则接受 seed 降级路径（已被 P0 修好），在 design 记录结论。

## 修复优先级

- P0（version str→int）必须修，这是崩溃直接原因。
- P1（boundary 调查）尽量修；若确属数据缺失无法在通用层修，记录结论、确保 seed 降级路径健壮即可。

## 兼容性

- 只动 `checkpoint.py`。`channel_versions` int 化对 MemorySaver（重写为 str）无影响——MemorySaver 的 `get_next_version(current: str|None)` 收到 int 会怎样？需在测试中验证 Memory 后端不回归（MemorySaver 重写签名是 `str|None`，传 int 可能报错）。**这是关键回归点**：修复后必须确认 Memory backend 的 seed 路径不崩。

  - 若 MemorySaver 对 int 报错：seed 写入的 version 类型需按 backend 适配，或 MemorySaver 路径不应走到 seed（Memory 是 fallback 后端，分叉场景罕见）。测试覆盖即可暴露。

## 回滚

- 单行改回 `"1"` 即回到崩溃状态；design 已留痕，回滚安全可控。
