# Implementation Plan

1. 在 core Harness authority 中重命名 `compact_zh` 概念为默认中文简洁 Harness，建立统一常量和显式 provider 注册入口。
2. 删除 `ShortTodoListMiddleware`、short Todo factory 和共享 profile 对 vendor Todo 的排除；让默认 profile 保留一个原生 `TodoListMiddleware`，由 localization middleware 改写最终提示/工具描述。
3. 将 Harness 注册责任从 `persona.py` 移到 core authority/bootstrap，保持三个 provider 与 fresh-process 行为。
4. 简化 Team overlay：只排除唯一的 `TodoListMiddleware`；让 request fallback 使用 core 共享常量，保持 registry save/restore 和 unresolved-key 兜底。
5. 更新 Team/Fast/Search、SOP 相关注释和命名，删除非归档源码/测试中的旧模式术语。
6. 更新 Harness spec 为唯一默认中文简洁实现，并记录 Todo 是 agent capability 而非语言模式。
7. 调整/新增测试：
   - Fast/Search 最终请求包含一个中文 Todo；
   - Team main + role subagent 最终请求无 Todo；
   - provider resolution、schema equality、动态 task、vendor hash、fresh process、direct-first import；
   - Team registry restore/unresolved fallback；
   - `todo:updated` 事件兼容。
8. 运行验证并由 fresh `trellis-check` 全量复核。

## Validation Commands

```powershell
python -m pytest tests/agents/core/test_harness_prompt_overrides.py -q
python -m pytest tests/agents/core/test_subagent_prompts.py tests/agents/test_team_harness_profile.py tests/agents/test_team_tool_exclusion_middleware.py -q
python -m pytest tests/agents/test_team_agent_sandbox_support.py tests/agents/test_team_agent_sop_tool_hook.py tests/agents/test_disabled_skills_config_propagation.py -q
python -m pytest tests/unit/agents/test_existing_agents_regression.py -q
python -c "import src.infra.tool.deferred_manager"
ruff check src/agents/core src/agents/team_agent tests/agents
```

## Rollback Points

- 完成 core default profile 与测试后先跑第一组 Harness tests；失败时不继续 Team overlay。
- Team overlay 完成后必须先验证 Fast/Search Todo 仍存在，再验证 Team Todo 消失。
- 不触碰 DeepAgents/LangChain 依赖锁；若公共 API 无法支撑设计，停止并回到规划，不通过私有 monkey patch 扩大范围。

