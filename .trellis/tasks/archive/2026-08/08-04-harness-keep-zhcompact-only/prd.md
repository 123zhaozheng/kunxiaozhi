# PRD: 删除 legacy / compact_en harness 模式，只保留 compact_zh

## 背景

`07-20-compress-agent-harness-prompts` 任务引入了 `AGENT_HARNESS_MODE` 三模式开关：

| 模式 | 用途 | 内容 |
|------|------|------|
| `legacy` | 压缩前完整回退 | vendor 原生 middleware，无本地化覆盖 |
| `compact_en` | 实验回退 | 压缩英文 harness |
| `compact_zh` | 生产默认 | 高密度简体中文 harness |

产品已稳定在 `compact_zh`。本任务**删除 `legacy` 与 `compact_en` 两条路径**，
将 harness 提示词 / 工具 schema 本地化固化（hardcode）为 compact_zh，并移除模式开关机制。

## 需求

1. 删除模式机制：`HarnessMode` 类型、`normalize_harness_mode`、`get_active_harness_mode`、
   `AGENT_HARNESS_MODE` 设置项（含后端定义、前端设置 UI、i18n、重启必需设置列表、注册常量）。
2. `select_harness_text` / `catalog_for_mode` / `build_short_todo_middleware` /
   `build_harness_extra_middleware` / `build_sop_guidance_section` 等去掉 mode 参数与分支，固化 zh 内容。
3. 删除英文 catalog 常量（`COMPACT_EN_BEHAVIOR_GUIDE`、`_EN_TOOLS`、`_EN_FIELDS`、`_EN_*` 文案）。
4. team / search / fast 三个 agent 的 system prompt 构建改为纯 compact_zh，
   删除 `_HARNESS_MODE` 条件分支（如 member_label、`能力`/`指令` 标题等）。
5. deepagents `HarnessProfile` 注册路径保留 compact_zh 行为；`legacy` 的 "vendor 原生 middleware" 路径删除。
6. 行为等价性：compact_zh 当前行为不得改变。工具名、schema 属性名、枚举值、
   `Current task start time` 等机器契约标识保持字节级稳定。

## 验收标准

- [ ] `src/`、`tests/`、`frontend/` 中不再有 `legacy` / `compact_en` / `HarnessMode` /
      `AGENT_HARNESS_MODE` 引用（纯历史注释除外）。
- [ ] 现有测试全部通过；harness 相关测试更新为无模式形态（删除模式枚举/参数化用例）。
- [ ] 设置 UI / i18n / 重启必需设置列表中无 harness 模式残留。
- [ ] `.trellis/spec/backend/agent-harness.md` 同步更新。
- [ ] 无行为回归：compact_zh 渲染出的 system prompt 与工具 schema 与删除前逐字一致
      （以现有测量/快照测试或新增对比测试验证）。

## 非目标

- 不改动工具 API / 调用契约。
- 不翻译/改名工具名、schema 属性名、枚举值、供应商 key、机器解析的哨兵标题。
- 不改变 prompt cache 块结构 / middleware 顺序。

## 开放问题（规划时与用户确认）

- `AGENT_HARNESS_MODE` 设置从设置 UI 移除后，"harness" 子分类是否还有其他设置项需要保留分类。
- 删除后命名策略：zh 常量是否去掉 `_ZH` 后缀（避免无意义后缀），还是保留后缀最小改动。
