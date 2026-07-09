# 执行计划：无沙箱 Fast Agent 文档附件处理

## 实现顺序

### 阶段 1：配置层（无依赖，可独立验证）

- [ ] 1.1 `src/kernel/config/base.py`：加 5 字段 `ENABLE_DOCUMENT_PARSE: bool = False`、`MINERU_API_BASE_URL: str = "http://localhost:8000"`、`MINERU_API_KEY: str = ""`、`DOCUMENT_PARSE_MAX_BYTES: int = 52428800`、`DOCUMENT_PARSE_MAX_OUTPUT_CHARS: int = 50000`
  → 验证：`python -c "from src.kernel.config import settings; print(settings.ENABLE_DOCUMENT_PARSE)"`
- [ ] 1.2 `src/kernel/config/definitions.py`：加 `DOCUMENT_PARSE` category 枚举 + 5 个配置项定义（复刻 `ENABLE_AUDIO_TRANSCRIPTION` 块结构）。`ENABLE_DOCUMENT_PARSE`/`MINERU_API_BASE_URL`/`MINERU_API_KEY` 设 `frontend_visible: True`；`DOCUMENT_PARSE_MAX_BYTES`/`DOCUMENT_PARSE_MAX_OUTPUT_CHARS` 不设（内部参数）
  → 验证：`grep DOCUMENT_PARSE src/kernel/config/definitions.py` 出现 5 项
- [ ] 1.3 `src/kernel/schemas/setting.py`：若 `SettingCategory` 枚举需加 `DOCUMENT_PARSE`，在此扩展
  → 验证：grep 无报错

### 阶段 2：MinerU 适配层（独立模块，可单测）

- [ ] 2.1 新建 `src/infra/tool/mineru_client.py`：`MinerUClient` 类，`parse_bytes(filename, content, content_type) -> str`，httpx 异步 POST `/file_parse`，headers 含 ngrok-skip + 可选 Bearer，data={return_md:true,...}，files={files:(filename,content,content_type)}，取 results 首项 md_content，指数退避重试 max_retries 次
  → 验证：单测 mock httpx，断言请求 form 字段 + 响应解析
- [ ] 2.2 新建 `tests/infra/tool/test_mineru_client.py`：mock httpx 返回 `{"results": {"a": {"md_content": "# T"}}}`，断言返回 `"# T"`；mock 超时重试 3 次后抛错
  → 验证：`uv run pytest tests/infra/tool/test_mineru_client.py -q`

### 阶段 3：工具实现（依赖阶段 2）

- [ ] 3.1 新建 `src/infra/tool/read_document_tool.py`：复刻 `audio_transcribe_tool.py` 结构。`@tool read_document(url, runtime)` 单参数。流程：`_resolve_url` → httpx 流式下载到 SpooledTemporaryFile（上限 `DOCUMENT_PARSE_MAX_BYTES`）→ `MinerUClient.parse_bytes` → 截断到 `DOCUMENT_PARSE_MAX_OUTPUT_CHARS` → 返回 Markdown（成功）或 `{"error":...}` JSON（失败）
  → 验证：单测覆盖成功/超限/MinerU 失败/空 url 四路径
- [ ] 3.2 新建 `tests/infra/tool/test_read_document_tool.py`：mock 下载流 + mock MinerUClient，断言四路径返回值
  → 验证：`uv run pytest tests/infra/tool/test_read_document_tool.py -q`
- [ ] 3.3 `src/infra/tool/internal_registry.py`：`build_internal_tools()` 加 `if settings.ENABLE_DOCUMENT_PARSE: tools.append(get_read_document_tool())`（仿 `ENABLE_AUDIO_TRANSCRIPTION` 块）
  → 验证：grep `read_document` internal_registry 命中

### 阶段 4：前端配置（依赖阶段 1 的 category）

- [ ] 4.1 `frontend/src/types/settings.ts`：加 `DOCUMENT_PARSE` category 类型
- [ ] 4.2 `frontend/src/components/panels/SettingsPanel.constants.ts`：加 DOCUMENT_PARSE 配置块（label/desc/icon），复刻 AUDIO_TRANSCRIPTION 块
- [ ] 4.3 `frontend/src/components/panels/SettingsPanel.tsx`：渲染新 category（若常量驱动则自动，确认）
- [ ] 4.4 `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json`：加 `settingLabel.ENABLE_DOCUMENT_PARSE`、`settingDesc.ENABLE_DOCUMENT_PARSE`、`MINERU_API_BASE_URL`、`MINERU_API_KEY`、category 名，5 语言齐全
  → 验证：`grep -l DOCUMENT_PARSE frontend/src/i18n/locales/*.json` 命中 5 文件

### 阶段 5：集成验证

- [ ] 5.1 启动后端，配置 `ENABLE_DOCUMENT_PARSE=true` + `MINERU_API_BASE_URL` 指向内网 MinerU
- [ ] 5.2 Fast Agent 会话上传 PDF，提示"详细阅读本文档"，确认模型调用 read_document 返回正文 Markdown
- [ ] 5.3 上传 DOCX/PPTX/XLSX 各一，确认四类型均可解析
- [ ] 5.4 WeCom 入站上传 PDF（配好 `APP_BASE_URL`），确认 url 带完整 host，模型能调工具
- [ ] 5.5 关闭 MinerU 服务，确认工具返回错误 JSON 而非崩溃，对话继续
- [ ] 5.6 上传超 `DOCUMENT_PARSE_MAX_BYTES` 文件，确认被拒并返回明确错误
- [ ] 5.7 `ENABLE_DOCUMENT_PARSE=false`，确认工具不注册，模型看不到，行为同现状

## 验证命令汇总

```bash
# 后端单测
uv run pytest tests/infra/tool/test_mineru_client.py tests/infra/tool/test_read_document_tool.py -q

# 配置加载
uv run python -c "from src.kernel.config import settings; print(settings.ENABLE_DOCUMENT_PARSE, settings.MINERU_API_BASE_URL)"

# 前端 i18n 完整性
grep -l DOCUMENT_PARSE frontend/src/i18n/locales/*.json

# 类型检查
cd frontend && npm run typecheck
```

## 风险点与回滚

- **风险1：内网 MinerU 接口差异**。MVP 按 check-yg local 契约实现，若实测内网版路径/字段不同，改 `MinerUClient` 一个文件即可，不影响工具层。
- **风险2：超长文档输出爆上下文**。靠 `DOCUMENT_PARSE_MAX_OUTPUT_CHARS` 截断兜底，默认 50k chars（~12k tokens）。
- **风险3：MinerU 同步解析慢（数十秒）**。MVP 接受，模型等待。二期加缓存。
- **回滚点**：`ENABLE_DOCUMENT_PARSE=false` 立即回退到现状。代码层面纯新增，删除新增文件 + 反注册一行即彻底回滚。

## Review Gate

阶段 3 完成后（工具可单测通过）暂停，自检：
- [ ] 工具签名只有 `url` 单参数（MVP 不暴露 key）
- [ ] 摘要零改动确认（`git diff src/agents/core/node_utils.py` 应为空）
- [ ] context.py 零改动确认（`git diff src/agents/*/context.py` 应为空）
- [ ] 三个 context 的工具加载都经 `get_internal_tools_for_user`（已确认 Team 复用 Fast）

通过后进入阶段 4 前端。
