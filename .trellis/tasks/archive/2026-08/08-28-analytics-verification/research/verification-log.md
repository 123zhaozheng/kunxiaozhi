# R1 全量自动化验证日志（2026-08-28）

环境：Windows 10 + Git Bash；Python 经 `uv`；前端 `pnpm` + Node v24.3.0；
本地 MongoDB 容器（mongo:8.2.5，localhost:27017）在运行，供真实聚合层测试使用。
验证对象：08-28-analytics-dashboard-rebuild 全部子任务完成后的工作区（含 R3 删除）。

## 1. 后端

### ruff

```
$ uv run ruff check src/ tests/
Found 3 errors.（均为既有问题，不在本次改动文件中）
  src\api\main.py:7:1            I001 import 排序（既有）
  tests\infra\agent\test_wecom_session_owner.py:49:61    W292（既有）
  tests\infra\persona_preset\test_dify_kb_agent_options.py:64:46  W292（既有）

$ uv run ruff check src/api/routes/analytics.py src/infra/analytics/ \
      src/kernel/schemas/analytics.py src/kernel/schemas/__init__.py
All checks passed!   # 本次改动的全部文件
```

全仓 `ruff check .` 共 113 条：.trellis 52 / .claude 19 / .cursor 17 / .qoder 13 /
.codex 9（工具配置目录，既有）+ 上述 3 条。改动文件零违规。

### mypy

```
$ uv run mypy src/
src\infra\tool\mineru_client.py:101: error: Need type annotation for "first"  [var-annotated]
Found 1 error in 1 file (checked 397 source files)
```

唯一错误为既有（mineru_client，与 analytics 无关）。

### pytest（tests/api + tests/infra 全量）

```
$ uv run pytest tests/api tests/infra -q --continue-on-collection-errors
9 failed, 1647 passed, 31 warnings, 1 error in 37.76s
```

9 条失败 + 1 条收集错误全部为既有（逐一核对，均与 analytics 无关）：

| 失败用例 | 归属 |
| --- | --- |
| test_human_wait.py::test_wait_for_response_awaits_cancelled_distributed_wait_cleanup | 审批等待 |
| test_shared_page_route.py ×2（service worker / offline 页） | 共享页静态资源 |
| wecom/test_preferred_agent_resolve.py | 企微 |
| test_sandbox_grep_timeout.py | 沙箱 |
| skill/test_loader_prompt.py | 技能加载 |
| test_runtime_services.py ×2 | 运行时服务 |
| test_s3_storage_service.py | S3 |
| ERROR tests/infra/share/test_storage_limits.py | 与 tests/infra/envvar/ 同名基名冲突（收集错误，既有） |

### analytics 专项（15 个测试文件）

```
$ uv run pytest tests/api/routes/test_analytics_*.py tests/infra/test_analytics_*.py -q
128 passed
```

含 `test_analytics_cross_consistency.py` 9 条：7 条路由层跨层一致性
（五条等式 + 区间一致性守卫）+ 2 条真实 MongoDB 集成（`lambchat_cross_consistency_test`
独立测试库，跑完即删；快照层在测试中被禁用，直测实时聚合管线）。

## 2. 前端

```
$ pnpm run build     # tsc 类型检查 + vite 产物：通过（PWA 1022 条 precache）
$ pnpm run lint      # eslint .：零告警
```

### tsx 全量测试

```
$ find src -name "*.test.ts*" | xargs -n 15 npx tsx --test
tests 807 / pass 763 / fail 44
```

44 条失败为既有环境性失败（聊天渲染 / 预览 / 团队编辑器 / WebSocket 等，
无一涉及 analytics）。**基线证明**：`git stash push frontend/src/services/api/analytics.ts`
后重跑同一套件，结果完全相同（807/763/44），随即 `git stash pop` 恢复。

### analytics 专项（6 个测试文件）

```
$ npx tsx --test src/components/panels/analytics/__tests__/*.test.ts \
      src/components/panels/__tests__/analyticsUsageSection.test.ts \
      src/i18n/__tests__/usageReportKeys.test.ts \
      src/services/api/__tests__/analyticsExport.test.ts
tests 31 / pass 31 / fail 0
```

（一致性、筛选契约、KPI 派生、usage 版面、i18n 键守护、CSV 导出。）

## 3. R3 删除验证

见同目录 `removal-evidence.md` E6：三层 + schema + 前端孤儿客户端方法删除后，
全仓无残留引用，上述全部检查通过。

## 结论

本次任务引入的改动全部通过自动化验证；所有失败项均以基线对照或文件归属
证明为既有问题，未新增任何回归。
