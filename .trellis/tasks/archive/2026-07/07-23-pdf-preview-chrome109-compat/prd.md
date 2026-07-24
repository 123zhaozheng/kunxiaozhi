# Fix PDF preview crash on Chrome 109

## Goal

内网 Chrome 109 打开 AI 生成的 PDF 侧边栏预览时，前端不再因 `Promise.withResolvers is not a function` 整页崩溃；PDF 预览在兼容构建下可正常加载或优雅降级。

## Background

- 自测场景：聊天中 AI 生成 PDF → 侧边栏准备打开预览 → 前端崩溃。
- 致命报错：`Promise.withResolvers is not a function`（触发全局 ErrorBoundary「出了点问题」）。
- 附带噪音：`GET /api/feedback/?skip=0&limit=100&session_id=... 403`（缺 `feedback:read`），**本任务不处理**。
- 根因：`PdfPreview` 使用 `react-pdf` + `pdfjs-dist@5.4.296` 的 **modern build**（`build/pdf*.mjs`），其中直接调用 `Promise.withResolvers()`；Chrome 109 不支持该 API（需 Chrome ≥119）。
- `pdfjs-dist` 已提供 **legacy build**（`legacy/build/*`），内置 `Promise.withResolvers` 等 polyfill。

## Scope

### In scope

- 前端 PDF 预览路径兼容 Chrome 109（及同类缺 `Promise.withResolvers` 的 Chromium）。
- 将 pdf.js 主库 + worker 切到 legacy 构建（或等价 polyfill 方案），保证 worker/main 版本一致。
- 回归测试锁定「使用 legacy worker / 不依赖宿主 `Promise.withResolvers`」。
- 现有 PDF 预览行为保持：连续滚动、缩放、手势、加载失败时新窗口打开 fallback。

### Out of scope

- `/api/feedback` 403 权限/调用方问题。
- 降级 `react-pdf` / `pdfjs-dist` 大版本（优先 legacy 构建）。
- 全面浏览器 matrix 兼容（以 Chrome 109 内网基线为准）。
- 非 PDF 文档预览类型。

## Constraints

- 仅改前端构建/PDF 预览相关文件，避免无关重构。
- main 与 worker 必须同属 modern 或同属 legacy，不可混用。
- 保持现有 `react-pdf` / `pdfjs-dist` 版本号（`^10.4.0` / `^5.4.296`）除非方案证明必须改。
- 内网环境无法依赖 CDN 拉 worker。

## Acceptance Criteria

- [ ] 在不支持 `Promise.withResolvers` 的运行时（模拟 Chrome 109 / 删除 API）加载 PDF 预览模块时，不再抛出未捕获的 `Promise.withResolvers is not a function`。
- [ ] `PdfPreview` 的 worker 指向 `pdfjs-dist` **legacy** worker（或文档化的等价兼容路径）。
- [ ] 通过 Vite resolve alias（或同等构建配置）使 `react-pdf` 解析到的 `pdfjs-dist` 主入口为 legacy 构建。
- [ ] 现有 PDF 相关单测通过；新增/更新测试锁定 legacy worker 路径与版本兼容约束。
- [ ] 打开 PDF 侧边栏失败时仍展示组件内 fallback（若 PDF 本身损坏），而不是整页 ErrorBoundary 白屏。
- [ ] 不修改 feedback API 权限逻辑（本任务范围外）。

## Notes

- 内网目标浏览器：Chrome 109。
- 实现后由 implement agent 改代码；check 验证测试与 diff 范围。
