# R3 存废判定取证（removal-evidence）

任务：08-28-analytics-verification；时间：2026-08-28；Git Bash。

## E1 /users/heatmap 前端引用

```
$ grep -rn -i "heatmap" frontend/src
frontend/src/i18n/__tests__/usageReportKeys.test.ts:158:  "analytics.users.heatmap",
frontend/src/i18n/__tests__/usageReportKeys.test.ts:159:  "analytics.users.heatmapHint",
```

仅 i18n 守护测试的 removedKeys（断言这些孤儿 key 已被删），无组件调用。后端链路：

```
$ grep -rn "get_users_heatmap\|HeatmapCell\|HeatmapResponse" src/ tests/ | grep -v __pycache__
src/api/routes/analytics.py:28:    HeatmapResponse,
src/api/routes/analytics.py:120:@router.get("/users/heatmap", response_model=HeatmapResponse)
src/api/routes/analytics.py:121:async def get_users_heatmap(
src/api/routes/analytics.py:126:) -> HeatmapResponse:
src/api/routes/analytics.py:129:    cells = await manager.get_users_heatmap(s, e)
src/api/routes/analytics.py:130:    return HeatmapResponse(cells=cells)
src/infra/analytics/manager.py:21:    HeatmapCell,
src/infra/analytics/manager.py:54:    async def get_users_heatmap(
src/infra/analytics/manager.py:56:    ) -> list[HeatmapCell]:
src/infra/analytics/manager.py:57:        return await self.storage.get_users_heatmap(start, end)
src/infra/analytics/storage.py:37:    HeatmapCell,
src/infra/analytics/storage.py:350:    async def get_users_heatmap(
src/infra/analytics/storage.py:354:    ) -> list[HeatmapCell]:
src/infra/analytics/storage.py:389:        cells: list[HeatmapCell] = []
src/infra/analytics/storage.py:393:                HeatmapCell(
src/kernel/schemas/analytics.py:37:class HeatmapCell(BaseModel):
src/kernel/schemas/analytics.py:45:class HeatmapResponse(BaseModel):
src/kernel/schemas/analytics.py:48:    cells: list[HeatmapCell] = Field(default_factory=list, description="热力图单元格列表")
src/kernel/schemas/__init__.py:18:    HeatmapCell,
src/kernel/schemas/__init__.py:19:    HeatmapResponse,
src/kernel/schemas/__init__.py:146:    "HeatmapCell",
src/kernel/schemas/__init__.py:147:    "HeatmapResponse",
```

## E2 /tokens/by-preset 前端引用（子2 遗留项，子3 已删饼图）

```
$ grep -rn "by-preset\|byPreset" frontend/src | grep -v feedback
frontend/src/i18n/locales/en.json:301:      "byPreset": "Feedback by persona",
frontend/src/i18n/locales/en.json:302:      "byPresetHint": "Up/down votes per persona",
frontend/src/i18n/locales/ja.json:301:      "byPreset": "エージェント別フィードバック",
frontend/src/i18n/locales/ja.json:302:      "byPresetHint": "エージェントごとの高評価/低評価",
frontend/src/i18n/locales/ko.json:301:      "byPreset": "에이전트별 피드백",
frontend/src/i18n/locales/ko.json:302:      "byPresetHint": "에이전트별 긍정/부정 평가",
frontend/src/i18n/locales/ru.json:301:      "byPreset": "Отзывы по агентам",
frontend/src/i18n/locales/ru.json:302:      "byPresetHint": "Положительные/отрицательные оценки по агентам",
frontend/src/i18n/locales/zh.json:301:      "byPreset": "按 Persona 分反馈",
frontend/src/i18n/locales/zh.json:302:      "byPresetHint": "按 Persona 统计好评/差评",
frontend/src/i18n/__tests__/usageReportKeys.test.ts:168:  "analytics.tokens.byPreset",
frontend/src/i18n/__tests__/usageReportKeys.test.ts:169:  "analytics.tokens.byPresetHint",
(exit 0)
```

## E3 /tokens/trend 前端引用

```
$ grep -rn "tokens/trend\|tokensTrend\|TokensTrend" frontend/src
(exit 1)
```

## E4 后端内部调用方（仅自身三层链路，无其它调用方）

```
$ grep -rn "get_tokens_by_preset\|get_tokens_trend" src/ tests/ | grep -v __pycache__
src/api/routes/analytics.py:197:async def get_tokens_by_preset(
src/api/routes/analytics.py:210:    items = await manager.get_tokens_by_preset(s, e, limit=limit)
src/api/routes/analytics.py:215:async def get_tokens_trend(
src/api/routes/analytics.py:223:    items = await manager.get_tokens_trend(s, e)
src/infra/analytics/manager.py:69:    async def get_tokens_by_preset(
src/infra/analytics/manager.py:72:        return await self.storage.get_tokens_by_preset(start, end, limit=limit or 10)
src/infra/analytics/manager.py:84:    async def get_tokens_trend(
src/infra/analytics/manager.py:87:        return await self.storage.get_tokens_trend(start, end)
src/infra/analytics/storage.py:475:    async def get_tokens_by_preset(
src/infra/analytics/storage.py:619:    async def get_tokens_trend(
```

## E5 保留项

- /feedback/by-preset、/tokens/by-model：前端活跃调用（反馈概览、模型 Token 环图），保留
- /overview、/sessions/trend 后端端点：前端客户端方法已无组件调用（孤儿），但仓外调用方无法排除，保留端点、删前端客户端方法

## E6 删除执行与验证（2026-08-28）

已删除（三层 + schema 导出 + 前端孤儿客户端方法）：

- `/users/heatmap` 路由 + `manager.get_users_heatmap` + `storage.get_users_heatmap` + `HeatmapCell`/`HeatmapResponse` schema（含 `schemas/__init__.py` 导入与 `__all__`）
- `/tokens/by-preset` 路由 + `manager.get_tokens_by_preset` + `storage.get_tokens_by_preset`
- `/tokens/trend` 路由 + `manager.get_tokens_trend` + `storage.get_tokens_trend`
- 前端 `analyticsApi.getOverview`、`analyticsApi.getSessionsTrend` 及对应类型导入（后端 `/overview`、`/sessions/trend` 端点按 E5 保留）

删除后验证：

```
$ grep -rnE "Heatmap|get_users_heatmap|get_tokens_by_preset|get_tokens_trend|tokens/by-preset|tokens/trend|users/heatmap|getOverview|getSessionsTrend" src/ frontend/src/
（无匹配）

$ uv run ruff check src/api/routes/analytics.py src/infra/analytics/ src/kernel/schemas/analytics.py src/kernel/schemas/__init__.py
All checks passed!

$ uv run pytest <15 个 analytics 测试文件> -q
128 passed（含真实 MongoDB 跨层一致性 9 条）

$ pnpm run build    # tsc + vite：通过
$ pnpm run lint     # eslint：通过
$ npx tsx --test <6 个 analytics 前端测试>
# pass 31 / fail 0
```
