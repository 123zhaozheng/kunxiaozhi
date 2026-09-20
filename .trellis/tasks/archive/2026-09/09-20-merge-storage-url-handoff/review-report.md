# Review 报告：kunxiaozhi 09-18 存储 URL 与 read_document 交接包合入

- Review 对象：`git diff c68ecc27..HEAD`（5 提交：1b041089 / 7f8d9c2f / aa4f4232 / 936adf1c / 1bdde899）
- 方式：只读分析 + 定向测试验证（未改任何源码/测试）
- 验证：`uv run pytest tests/infra/storage/ tests/infra/tool/ tests/api/test_storage_content_public_path.py tests/agents/test_attachment_lifecycle.py tests/api/test_auth_middleware_whitelist.py tests/infra/test_user_storage_quota.py -q` → **292 passed**；ruff 对全部涉改文件 → **All checks passed**；src 无冲突标记残留；`.trellis/.developer` 未入库（`git show HEAD` 报 not in HEAD），本地为 zhaozheng。

---

## 🔴 阻断（必须修）

无。

---

## 🟡 建议（应修）

1. **`src/infra/tool/read_document_tool.py:371` 探测触发条件未按设计收窄；`_recover_filename`（:136）仍是 GET**
   design.md §7 与 PRD R5 声明：触发收窄为「仅托管 `/content` 结尾且未命中已知扩展名」、GET 改 HEAD。实际两处都没做——仍是 93948609 原样 `file_kind is None and "." not in filename`。后果：沙箱/Skill 无扩展名路径（如 `/workspace/Makefile`）会触发 `_recover_filename` 对非 http 路径发 httpx GET，抛 UnsupportedProtocol 落进 except，产生一次无谓网络尝试 + warning 日志，最终才返回 `Unsupported file type`。结果正确但与设计不符，且「给本地路径发网络请求」是浪费路径。
   证据：`read_document_tool.py:371-377`（触发未收窄）、`:136-146`（GET、通用 except）。

2. **`src/infra/tool/read_document_tool.py:107-114` `_resolve_url` 成为死代码**
   它是本次改动唯一的孤儿（调用点被 `resolve_document_source` 替换，grep 全仓仅剩 audio_transcribe_tool.py 的同名独立函数）。按 CLAUDE.md「自己改动产生的孤儿应移除」应删。

3. **`MINERU_RETURN_IMAGES` 端到端 no-op，README 却列为可调配置**
   `src/kernel/config/base.py:447` 定义、`mineru_client.py:64-67/:87/:91` 已接线、`extract_images`（`mineru_client.py:150`）有完备实现与测试，但全仓唯一生产调用方 `read_document_tool.py:427-432` 既不传 `return_images=True` 也不消费 `images` 字段——置 true 无任何效果。HANDOFF §4 已如实声明（AC6 半做的既定边界），但 README §4 的配置表会误导部署同学；建议在 README 或该配置注释标注「当前未接入，预留」。
   证据：grep `extract_images|return_images` 仅命中 mineru_client.py 自身与 config；`read_document_tool.py:432` 只取 `md_content`。

4. **`src/infra/tool/read_document_tool.py:341-343` 参数描述与 `:347-356` docstring 未同步新契约**
   `url` 参数 Annotated 仍写「Absolute URL or /api/upload/file/<key> path」，docstring 无图片分支。模型可见描述以 `harness_prompt_overrides.py`（已更新）为准，但 `@tool` 自带 schema 是第二事实源，harness 覆盖一旦失效就会暴露旧文案。属一致性债务。

---

## 🟢 可接受（核实通过的要点与低危备注）

### B. 安全面（无绕过）

- **鉴权豁免正则** `^/api/storage/files/[0-9a-f]{32}/content(?:/.*)?$`（`auth.py:68`）。实测（本地 python 探针）：
  - `/content/../status`、`/content/..`、`/content/.` **会被豁免**（`(?:/.*)?` 的 `.*` 吞掉点段）。但 Starlette/FastAPI 路由不做点段归一化，这些请求仍落到 content 端点（`filename` 被 `del` 丢弃，`storage.py:248`），路由表（`storage.py:64-236`）在 `/files` 下只有 list/status/DELETE/batch-delete/content 五组，`content/..` 形态无法改写命中其它路由；反向若上游代理先归一化，app 收到的已是归一路径、不再匹配豁免。**结论：无实际绕过**。可选加固：正则排除点段（如 `(?:/(?!\.\.?/)[^ ].*)?`），把这条依赖「框架不归一化」的隐式前提显式化。
  - `content%2F..%2Fstatus`、大写 `/CONTENT`、非 32hex、`contentx`、`/files/{id}` DELETE、`files/status`、`batch-delete`、`usage` 均 401（实测 + `test_storage_content_public_path.py` 负向锁定）。
- **裸存储 key 防线完整，无其它裸 key 入口**：
  - `document_source.py:83-87` 不带前导 `/` 一律 `file_missing`，不猜测补斜杠（测试锁定）。
  - legacy `/api/upload/file/{key}` 对 managed 行**强制 404**（`upload.py:1561-1564`），物理 key 不构成第二条授权路径；`..` 穿越被 `_has_traversal` 拦为 `file_forbidden`（实测含 `/skills/../..` 形态）。
  - `get_content_file(allow_storage_key=True)` 其余调用点均在 Bearer 保护的路由内（upload.py:334/1208/1367/1459/1535）。
- **Content-Disposition 注入面关闭**：sanitize 先按 `\` `/` 取 basename（反斜杠不可能存活）、移除 C0/C1/NUL（实测 `a\nb.pdf` → `ab.pdf`，CR/LF 无法进入 header）、ASCII 引号 `%22`（`test_content_url.py:85-88`）、非 ASCII 全量 `quote(safe="")` + latin-1 编码验证。`disposition` 参数为服务端常量。

### D. 合并决策复核（叠加无行为缺口）

- `attachments.py:121-125`：`build_content_url("", file_id, name)`（相对路径+清洗文件名）→ `_absolute_storage_url`（补 APP_BASE_URL），与本地 f71efc09 主机前缀逻辑正交；`status_for_user`（`user_storage.py:2567-2574`）返回库内原始行（有 `name` 无 `url`），投影在 `_status_projection:107-110` 先取 DB 名再拼 URL，文件名来自权威库内数据。组合形态由 `tests/agents/test_attachment_lifecycle.py:158-183` 两条（有/无 APP_BASE_URL）锁定。
- 宽前缀删除后的调用点核对（grep 全仓 `/api/storage/files/`）：生产点全部收敛 `build_content_url`（upload.py 9 处、skill.py、wecom/handler.py、attachments.py），无手写残留；反解点 `upload.py:288`、`persona_preset/manager.py:28`、`team/manager.py:24` 的 `split(marker,1)[1].split("/",1)[0]` 对新形态天然兼容。tokenless 消费方只有两个（`read_document` 的 `_download_to_bytes`、`upload_url_to_sandbox` 的沙箱 urllib 下载，`upload_url_tool.py:42-59`），都只取 `/content` 形态 → 精确正则全覆盖；skill/wecom file_id 均为服务端 uuid hex 32（`skill/storage.py:189/216` 走 managed plan）→ 命中豁免。
- 前端 `AttachmentPreviewHost.tsx:57-67`：优先 `attachment.url` + `getFullUrl`（绝对 URL 原样返回，`api/config.ts:73-75`），新旧两种 URL 形态均兼容，本地 7bc040cb/93948609 改动保留完好。
- resurrect 路径（`user_storage.py:1626-1641`）：派生 key 真正传入 `begin_operation`（:1685），`persisted_retry_item=None` 使 :1677-1679 取全新 file_id/blob_id/请求侧新物理 key；`find_active_by_hash` 只命中 PENDING|ACTIVE。TOCTOU（检查后并发删除）窗口极小且下次重传自愈，🟢。

### C. 完成度核对（README §6 三项「未包含」均属实，无隐性半吊子）

- **vision 链路缺口属实且未半修**：`node_utils.py:188-191` 与 `vision_assist.py:58-62` 都读 `key`；managed 激活附件被 `attachments.py:120` 置 `key=""` → `describe_image` 恒 None、node_utils 走 URL 直通。是「整块未做」，不是改一半。
- **used_bytes 批量重算缺失属实**：只有 `storage.py:211` 单用户 reconcile 路由 + `jobs.py:84-86` 对卡死 operation 用户的定点 `reconcile_user`，无全量扫描任务。
- **回收站缺失属实**：storage 路由无 restore/recycle 端点，删除仍是墓碑。
- **半吊子扫描结果**：除上述 🟡3 的 `MINERU_RETURN_IMAGES`/`extract_images`（已声明）外，未发现「调用点改了但数据没迁移」类问题；`strip_server_local_image_links` 对所有 MinerU 输出生效（有 `<details>` 保留测试）；两处既有断言修改（wecom accounting / mineru client form data）与契约一致。

### A. 核心新模块质量

- `content_url.py`：清洗/截断/编码各边界正确（`..`→`download`、UTF-8 255 字节不切多字节、`quote(safe="")`）；RFC 5987 只发 `filename*` 无 `filename=` ASCII 兜底，属 RFC 6266 建议项缺失，主流浏览器均可用，🟢。`build_content_url` 不校验 `file_id` 形态——内部全部服务端 uuid hex 输入，硬ening 备注，🟢。
- `document_source.py`：优先级固定且有注释；无 backend 时 fail-closed；`read_backend_bytes` 空 content（`b""`）会被 `getattr(..., None)` 判假落 `file_missing`（空文件场景，语义可接受）；大写 scheme `HTTPS://` 会被拒（`startswith` 大小写敏感，与旧 `_resolve_url` 行为一致，🟢 备注）。

### E. 测试质量

约 500 行新测试均为行为断言（中间件走真实 `dispatch`+sentinel、MinerU 断言真实 form data wire 契约、重传断言 file_id/blob_id/物理 key/列表可见/in-flight 幂等），无测 mock 自身的问题。洞：
- 对象存储 + 非 ASCII 的端到端响应链路无测试（HANDOFF 已声明需真机 curl）；
- 豁免正则对 `/content/../` 形态的行为未锁定（当前依赖框架不归一化的结论没有回归保护）；
- 大写 32hex、`_classify_file` 对百分号编码名（`%E6..pdf` 后缀仍可判，实测 OK）无显式用例。
- 小瑕疵：`test_read_document_tool.py:689` 的恢复文件名测试用 `files/f9/content`（非 32hex），在新 resolver 下走的是 app_path fallback 而非 managed flavor，测试仍绿但语义漂移。

### 真机待确认项（本仓库内无法闭环）

1. `APP_BASE_URL` 未配置时 URL 退化为相对路径且无启动校验（`attachments.py:43-46`、`backend_utils.py:53`）。
2. `/api/chat/stream` 不传 `base_url`（HANDOFF 指出 `chat.py:307`）与 `/api/agents/{id}/stream` 行为不一致。
3. `MINERU_PARSE_EFFORT=high` 的解析耗时（high 走 two-step extract）。
4. 对象存储后端 + 非 ASCII 文件名的完整 ASGI 响应链路（header 构造已单测，链路未验）。
5. 5 个部署配置项（`MINERU_BACKEND` / `MINERU_PARSE_EFFORT` / `MINERU_IMAGE_ANALYSIS` / `MINERU_RETURN_IMAGES` / `APP_BASE_URL`）尚未落到 k8s yaml（09-20 PRD 已记待办）。

---

## 结论

合入质量高：5 个维度均无阻断项。URL 契约收敛、鉴权精确豁免、删除后重传修复、read_document 分发与归属防线的实现与 spec/HANDOFF 声明一致，测试为行为级且定向全绿。4 项 🟡 均为一致性/债务类（探测收窄未做、死代码、no-op 配置的文档风险、schema 描述漂移），可随后续小提交清理；🟢 中最有价值的一条是给豁免正则的 `..` 容错补一条负向测试或收紧正则，把「框架不归一化」这个隐式前提固化下来。
