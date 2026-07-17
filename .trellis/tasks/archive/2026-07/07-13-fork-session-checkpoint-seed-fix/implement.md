# Implement — 分叉会话 checkpoint seed 修复

> Active task: `.trellis/tasks/07-13-fork-session-checkpoint-seed-fix`

## 执行清单

1. **P0 修复**：`checkpoint.py:492` `checkpoint["channel_versions"] = {"messages": "1"}` → `{"messages": 1}`（int）。 → 验证：`get_next_version(1, ...)` 返回 2，不抛 NotImplementedError。

2. **写复现测试**（先红后绿）：
   - 用 MongoDBSaver 的真实 alist/aput 路径（可用 `mongomock_motor` 或项目已有 checkpoint 测试夹具；若无则用 `MemorySaver` 但**注意 MemorySaver 重写了 get_next_version 用 str，掩盖 bug**——必须用 MongoDBSaver 或直接单测 `get_next_version` 行为）。
   - 场景：seed 一个 checkpoint（channel_versions 含 str/int）→ 模拟 `apply_writes` 调 `get_next_version` → 断言 int 不抛、str 抛。
   - 更进一步：端到端 fork → chat 不抛。

3. **P1 调查 boundary**：写测试构造源会话 `[Human, AI]` checkpoint 序列，调 `_find_fork_boundary_checkpoint(turn=1, type="assistant")`，打印每个 checkpoint 的消息类型与 human_count，定位 `_matches_fork_boundary` 失败原因。按结论修复或记录。

4. **回归 Memory backend**：确认 int version 不让 MemorySaver 的 `get_next_version(current: str|None)` 崩（签名是 str|None，传 int 需验证）。若崩，在 seed 处按 saver 类型适配，或在测试中确认 Memory 路径不走 seed。

5. **回归非分叉会话**：跑现有 session/fork 测试，确认正常会话路径不受影响。

## 验证命令

```powershell
# 语法
uv run python -c "import ast; ast.parse(open('src/infra/storage/checkpoint.py',encoding='utf-8').read()); print('ok')"

# 直接验证 get_next_version 行为（P0 核心）
uv run python -c "from langgraph.checkpoint.base import BaseCheckpointSaver; from langgraph.checkpoint.mongodb import MongoDBSaver; import inspect; print('mongo overrides:', 'get_next_version' in MongoDBSaver.__dict__)"

# checkpoint / fork 相关测试
uv run pytest tests/ -k "checkpoint or fork or session" -q

# 全量导入
uv run python -c "from src.infra.storage.checkpoint import seed_checkpoint_from_messages, clone_checkpoints_for_fork; print('import ok')"
```

## 注意

- **不要用 MemorySaver 验证 P0**——它重写了 get_next_version 为 str 方案，会掩盖 str-version bug。必须用 MongoDBSaver 或直接测 `get_next_version` 对 str/int 的行为。
- `versions_seen` 是否需补充节点 seen 版本：以测试驱动，不臆测。
- 只改 `src/infra/storage/checkpoint.py`（必要时扩到 `session/manager.py` 的 fork 调用，但优先收敛在 checkpoint.py）。
- 不允许 git commit（sub-agent 规范）。
- 复现测试若需 MongoDB，优先复用项目已有测试夹具/fixture（grep `tests/` 下 mongo checkpoint 用法）。
