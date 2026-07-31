# Persona Skill 实体化执行计划

## 1. 安装服务与测试

- [ ] 从 `src/api/routes/marketplace.py` 抽取 Marketplace → 用户 SkillStorage 的复制/安装逻辑到 `src/infra/skill/installation.py`。
- [ ] 定义“按名称存在”的单一 helper，避免 Persona、路由、测试各写一套判断。
- [ ] 保持手动安装 API 的 404/403/409 和成功响应兼容。
- [ ] 增加服务测试：本地同名直接复用、缺失时安装、缺少 `SKILL.md`、复制失败清理、重复调用幂等。

验证：

```powershell
uv run pytest tests/infra/skill/ tests/api/test_marketplace_routes.py -q
```

## 2. Persona use 实体化

- [ ] `PersonaPresetManager.use_preset` 改为“先本地名称检查，再预检缺失 Marketplace，再安装”。
- [ ] 删除消费者侧来源/版本同源校验；本地手工同名也直接复用。
- [ ] 仅在全部依赖成功后 increment usage/touch preference。
- [ ] snapshot 使用普通 `skill_names`，不再要求 runtime overlay。
- [ ] 增加旧 Persona 回退、混合本地/缺失依赖、不可用依赖、部分失败补偿测试。

验证：

```powershell
uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py -q
```

## 3. 移除 Persona Marketplace runtime overlay

- [ ] 停止 `resolve_persona_request` 注入 `agent_options.persona_marketplace_skills`。
- [ ] 清理 Fast、Search、Team context、graph、nodes 和 backend factory 的 refs 透传。
- [ ] 删除/退役 `PersonaSkillStorageOverlay` 及仅服务 overlay 的加载逻辑。
- [ ] 更新 Skills backend、prompt 和 Agent 回归测试，确保全部从用户 SkillStorage 读取。
- [ ] 新增真实组合测试：其他用户首次使用 Persona 后，`read_file`、`ls`、`transfer_file`、`transfer_path` 均可访问自动安装 Skill。

验证：

```powershell
uv run pytest tests/agents tests/infra/backend tests/infra/tool/test_transfer_file_tool.py -q
```

## 4. Persona 创建防重

- [ ] Schema/API 增加可选 `create_request_id`。
- [ ] Persona storage 增加 partial unique index及按 request ID 取回首次结果的幂等创建。
- [ ] 前端编辑器生成稳定 request ID，并使用同步 ref 防止确认回调重入。
- [ ] 增加后端并发重复 POST、前端重复确认测试。

验证：

```powershell
uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py -q
pnpm --dir frontend test
```

## 5. 跨层回归与收口

- [ ] 验证自动安装项出现在用户 Skills 列表，且切换 Persona 后永久保留。
- [ ] 验证本地同名 Skill 不被读取 Marketplace、覆盖或改 metadata。
- [ ] 验证 `disabled_skills` 语义不变。
- [ ] 验证旧公开 Persona、Fast、Search、Team、普通聊天和手动 Marketplace 安装。
- [ ] 运行格式、lint、类型检查和目标测试。

最终验证：

```powershell
uv run ruff check src tests
uv run mypy src
uv run pytest tests/persona_preset tests/api/test_persona_preset_routes.py tests/api/test_marketplace_routes.py tests/infra/skill tests/infra/backend tests/infra/tool/test_transfer_file_tool.py tests/agents -q
pnpm --dir frontend lint
pnpm --dir frontend type-check
pnpm --dir frontend test
```

## 风险文件

- `src/infra/persona_preset/manager.py`
- `src/api/routes/marketplace.py`
- `src/api/routes/chat.py`
- `src/infra/backend/skills_store.py`
- `src/infra/backend/deepagent.py`
- `src/agents/{fast_agent,search_agent,team_agent}/`
- `frontend/src/components/persona/PersonaEditorModal.tsx`
- `frontend/src/services/api/personaPreset.ts`

## Review Gates

1. 安装服务完成后，确认手动 Marketplace 安装 API 无行为漂移。
2. Persona use 完成后，确认本地同名优先且失败不留下半安装。
3. Overlay 清理后，全文搜索不得剩余 runtime `persona_marketplace_skills` 透传。
4. 创建幂等完成后，验证两个并发相同 request ID 只产生一条 Persona。

## Rollback Points

- 安装服务抽取可独立回滚，不改变数据。
- Persona 实体化可通过恢复 snapshot overlay 输出回滚；已安装用户 Skill 保留。
- Overlay 删除必须在全链路测试通过后单独提交/回滚。
- 创建幂等为向后兼容增量，可独立保留或回滚前端字段。
