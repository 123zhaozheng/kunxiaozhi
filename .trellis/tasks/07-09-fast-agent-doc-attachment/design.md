# 技术设计：无沙箱 Fast Agent 文档附件处理

## 1. 设计目标

让 Fast/Search/Team（无沙箱）模式下，用户上传文档附件后，模型可主动调用内置工具 `read_document` 读取正文 Markdown。不改附件摘要、不改 `build_human_message`、不依赖沙箱。

## 2. 架构边界

```
模型调用 read_document(url)
        │
        ▼
 resolve_url ── 绝对 URL 直接用；相对 /api/upload/file/<key> 用 base_url 拼前缀
        │
        ▼
 httpx 流式 GET 下载（复刻 audio_transcribe 下载段，含大小上限检查）
        │
        ▼
 MinerUClient.parse_bytes(filename, bytes, mime)
   ── multipart POST {MINERU_API_BASE_URL}/file_parse
   ── data: {return_md: "true", return_content_list: "false", return_images: "false"}
   ── headers: MinerU 内网所需（含 api_key 若配置）
        │
        ▼
 解析响应 {"results": {key: {"md_content": "..."}}}
 取首个 md_content → 截断到 MAX_OUTPUT_CHARS → 回灌模型
```

边界：
- 工具吃 `url`（主）+ 可选 `key`（备）。`key` 走 S3 `storage.download_file(key)` 直取字节，绕开 HTTP proxy（备用路径，适配 url 为空的场景）。
- 主路径 `url` 走 HTTP 下载，与 `audio_transcribe` 完全对称——不反解 key、不直接调 S3，复用已验证的下载+限流逻辑。
- 失败任何环节都返回 JSON `{"error": "..."}`，不抛异常，不阻断对话。

## 3. 数据流与契约

### 3.1 工具签名

```python
@tool
async def read_document(
    url: Annotated[str, "Document attachment URL from message summary. Absolute URL or /api/upload/file/<key> path."],
    key: Annotated[str | None, "Optional S3 storage key. If provided, downloads directly via S3, bypassing HTTP proxy. Use when url is empty."] = None,
    runtime: Annotated[ToolRuntime, InjectedToolArg] = None,
) -> str:
    """Download a document (pdf/docx/pptx/xlsx) and return its full text as Markdown."""
```

参数设计：
- `url`：主参数。模型从附件摘要里的 url 直接拿。相对路径 `/api/upload/file/<key>` 经 `_resolve_url`（复用 audio_transcribe）拼 base_url。
- `key`：备选。当 `APP_BASE_URL` 未配、WeCom 入站 url 为空时，模型若能从摘要拿到 key 可走 S3 直取。**摘要当前不含 key**，所以 key 路径是预留降级，MVP 可不暴露给模型（见 §7 待定）。**MVP 决策：只暴露 `url`，`key` 参数暂不加**——保持工具签名最简，与"摘要零改动"原则一致。

### 3.2 URL 解析与下载

复用 `audio_transcribe_tool._resolve_url`（`backend_utils.get_base_url_from_runtime`）：runtime.config 优先，fallback `settings.APP_BASE_URL`。下载用 `httpx.AsyncClient` 流式 + `SpooledTemporaryFile`，复刻 audio_transcribe 下载段（`audio_transcribe_tool.py:142-179`），上限用 `DOCUMENT_PARSE_MAX_BYTES`。

### 3.3 MinerU 适配层

新建 `src/infra/tool/mineru_client.py`：

```python
class MinerUClient:
    """内网 MinerU FastAPI 适配层。
    
    契约参考 check-yg/src/parsers/pdf_parser.py 的 MinerUClient（local 模式）。
    内网版差异（路径前缀/认证头）通过配置项注入，统一走 /file_parse multipart 上传。
    """

    def __init__(self, base_url: str, api_key: str | None = None,
                 timeout: int = 300, max_retries: int = 3, retry_delay: int = 2):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    async def parse_bytes(self, filename: str, content: bytes,
                          content_type: str = "application/pdf") -> str:
        """POST 字节给 MinerU，返回 Markdown。
        
        Returns md_content string. Raises on network/parse failure.
        """
        url = f"{self.base_url}/file_parse"
        headers = {"ngrok-skip-browser-warning": "true"}  # check-yg 内网经验
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = {
            "return_md": "true",
            "return_content_list": "false",
            "return_images": "false",
        }
        files = {"files": (filename, content, content_type)}
        # 指数退避重试 max_retries 次，参考 check-yg retry_delay
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, headers=headers, data=data, files=files)
            response.raise_for_status()
            result = response.json()
        results = result.get("results", {})
        first = next(iter(results.values()), {})
        return first.get("md_content", "")
```

设计要点：
- 同步契约对齐 check-yg 的 `MinerUClient`（POST /file_parse、multipart `files` 字段、data={return_md 等}、取 results 首项 md_content）。
- 改 `requests.Session` 为 `httpx.AsyncClient`（LambChat 全异步栈）。
- `api_key` 可选——内网版若需认证加 Bearer 头；无 key 时纯 ngrok 头（check-yg local 模式无认证）。
- 重试逻辑封装在客户端层，工具层不重复。
- content_type 按文件扩展名映射：pdf→application/pdf、docx→application/vnd.openxmlformats-officedocument.wordprocessingml.document 等。无映射时用 application/octet-stream（MinerU 自己嗅探）。

### 3.4 输出截断

MinerU 返回 Markdown 可能极长（数百页 PDF）。新增 `DOCUMENT_PARSE_MAX_OUTPUT_CHARS`（默认 50000，约 12k tokens）截断。截断时追加 `\n\n[... truncated, {total} chars total ...]` 提示模型内容被裁。MVP 不做分页/分段读——模型若需更多可再调一次（无状态，MinerU 重解析，二期加缓存）。

## 4. 配置项

新增到 `src/kernel/config/definitions.py`（复刻 `ENABLE_AUDIO_TRANSCRIPTION` 块结构）：

| Key | Type | Default | frontend_visible | depends_on | 说明 |
|-----|------|---------|-------------------|------------|------|
| `ENABLE_DOCUMENT_PARSE` | BOOLEAN | False | True | — | 总开关 |
| `MINERU_API_BASE_URL` | STRING | `http://localhost:8000` | True | `ENABLE_DOCUMENT_PARSE` | 内网 MinerU 地址 |
| `MINERU_API_KEY` | STRING | `""` | True | `ENABLE_DOCUMENT_PARSE` | 可选认证 |
| `DOCUMENT_PARSE_MAX_BYTES` | NUMBER | 52428800 (50MB) | False | `ENABLE_DOCUMENT_PARSE` | 下载上限 |
| `DOCUMENT_PARSE_MAX_OUTPUT_CHARS` | NUMBER | 50000 | False | `ENABLE_DOCUMENT_PARSE` | 输出截断 |

新增 `SettingCategory.DOCUMENT_PARSE`（`definitions.py` 的 category 枚举 + i18n）。`base.py` 加对应字段。

## 5. 工具注册

**关键简化**：Fast/Search/Team 三 agent 共享同一入口 `get_internal_tools_for_user`（`internal_registry.py`）。Fast 在 `fast_agent/context.py:188`、Search 在 `search_agent/context.py:205`、Team 复用 Fast 的工具加载（`team_agent/context.py` 注释确认 "reuses FastAgentContext tool/skill loading"）。

所以只需在 `build_internal_tools()`（`internal_registry.py:29`）加一行：

```python
if settings.ENABLE_DOCUMENT_PARSE:
    tools.append(get_read_document_tool())
```

三 agent 自动全有，无需改任何 context.py。启用时模型可见工具；未启用时行为同现状。MCP virtual server（`INTERNAL_MCP_SERVER_NAME`）自动接管策略过滤、配额、角色权限——与 audio_transcribe 同等待遇。

## 6. 失败降级矩阵

| 失败场景 | 工具行为 | 模型可恢复 |
|---------|---------|-----------|
| `ENABLE_DOCUMENT_PARSE=False` | 工具不注册，模型看不到 | —（现状） |
| `MINERU_API_BASE_URL` 未配 | 返回 `{"error": "MINERU_API_BASE_URL not configured"}` | 是 |
| url 解析失败（空 url + 无 key） | 返回 `{"error": "URL is empty, APP_BASE_URL may not be configured"}` | 是 |
| 下载超 size 上限 | 返回 `{"error": "Document exceeds {max} bytes"}` | 是 |
| MinerU 不可达/超时（重试耗尽） | 返回 `{"error": "MinerU parse failed: {detail}"}` | 是 |
| MinerU 返回空 md_content | 返回 `{"error": "Empty content from MinerU"}` | 是 |
| 不支持的文件类型（如 HTML） | 返回 `{"error": "Unsupported file type"}` | 是 |

所有错误返回 JSON 字符串，模型可回退到附件摘要里的 url，告诉用户"无法解析，请下载查看"。

## 7. 关键权衡

1. **HTTP 下载 vs S3 直取**：选 HTTP（经 `/api/upload/file/{key}` proxy）。与 audio_transcribe 完全对称，复用已验证下载逻辑，不引入"反解 key"的脆弱解析。代价：依赖 `APP_BASE_URL` 配好（否则 url 为空）。已由用户确认默认配置。

2. **只暴露 url 不暴露 key**：MVP 保持签名最简。摘要不含 key，模型拿不到 key 参数值，暴露了也没用。留 `key` 备选路径在 MinerUClient 内部能力里，二期若改摘要暴露 key 再启用。**MVP：工具只吃 `url` 单参数。**

3. **同步 POST 而非任务轮询**：内网 MinerU 走 `/file_parse` 同步返回（check-yg local 模式确认）。公开版才用任务轮询（create task → poll）。本工具只对接内网同步接口，不做轮询。

4. **不做结果缓存**：MVP 每次调用重解析。MinerU 同步解析数十秒级，重调有成本。二期参考 vision_assist 提案按 key 缓存 md_content。

5. **截断而非分页**：MVP 单次返回截断到 50k chars。超长文档模型只能看到前半。二期加分页参数（参考 mineru MCP 的 pages 字段）。

## 8. 兼容性

- 零侵入：不改 `node_utils.py`、不改 `context.py`、不改附件摘要、不改 `APP_BASE_URL` 机制。
- 纯新增：1 个新工具文件 + 1 个 MinerU 客户端文件 + config 项 + i18n + 注册一行。
- 启用开关默认 False，不影响任何现有部署。
- 与 vision_assist 对称：图片走 vision_assist（预处理注入），文档走 read_document（模型主动调）。两条独立路径，互不干扰。

## 9. 文件清单

新增：
- `src/infra/tool/read_document_tool.py` — 工具实现（复刻 audio_transcribe_tool 结构）
- `src/infra/tool/mineru_client.py` — MinerU 适配层
- `tests/infra/tool/test_read_document_tool.py` — 工具单测
- `tests/infra/tool/test_mineru_client.py` — 客户端单测（mock httpx）

修改：
- `src/kernel/config/definitions.py` — 加 5 个配置项 + DOCUMENT_PARSE category
- `src/kernel/config/base.py` — 加 5 个字段
- `src/infra/tool/internal_registry.py` — `build_internal_tools()` 加注册（1 行）
- `src/kernel/schemas/setting.py` — 若 category 枚举需扩展
- `frontend/src/types/settings.ts` — 新 category 类型
- `frontend/src/components/panels/SettingsPanel.constants.ts` — 新 category 配置块
- `frontend/src/components/panels/SettingsPanel.tsx` — 渲染新 category
- `frontend/src/i18n/locales/{en,zh,ja,ko,ru}.json` — settingDesc + settingLabel 5 语言

## 10. 待定（实现期确认，不阻断规划）

- 内网 MinerU 接口的具体差异（路径前缀、认证头字段名）——需用户提供内网接口文档或抓包。MVP 按 check-yg local 契约实现，配置项留 `MINERU_API_KEY` 兜底。
- MinerU 响应是否含 JSON middle_json——MVP 只取 md_content，若实测发现正文在别的字段再调。
- content_type 映射表完整度——MVP 覆盖 pdf/docx/pptx/xlsx/txt，其余 fallback octet-stream。
