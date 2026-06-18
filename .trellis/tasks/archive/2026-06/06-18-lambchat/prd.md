# 品牌重塑 LambChat → 昆小智 (KunXiaoZhi) 并移除介绍页

## Goal

将 LambChat 项目整体重塑为新产品「昆小智」(英文/技术标识 `KunXiaoZhi`)：删除 `localhost:3001/` 介绍页（LandingPage，路由 `/`）并改为直接跳转登录页；清除项目中全部 LambChat 品牌痕迹；删除绑定 GitHub 仓库地址的全部 UI 按钮与底层 release/版本检查基础设施（含文档克隆链接）。打造为独立新产品。

## What I already know

- 介绍页 = `frontend/src/components/landing/LandingPage.tsx`，由 `App.tsx:390` `<Route path="/" element={<LandingPage />} />` 挂载。删除该路由 + 整个 landing 目录，`/` 改为重定向到 `/auth/login`。
- 品牌 key 入口：`frontend/src/constants/index.ts` (`APP_NAME="LambChat"`、`GITHUB_URL`)、`BrandWordmark.tsx` (SVG 文本 "LambChat")、`manifest.json` (name/short_name)、Tauri/Android/iOS 原生配置、i18n 五语言、PWA (manifest/sw/offline.html/robots/sitemap)、后端 `src/kernel/config/base.py` (`GITHUB_URL` 默认值、可能的 APP_NAME)。
- LambChat 痕迹波及约 138 个文件（含 docs/README/.github/k8s/.trellis 归档等）。
- GitHub 用途分两类，边界已确认：
  - **品牌/release 绑定（全删）**：`src/infra/github_client.py`（release 抓取）、`src/api/routes/version.py`（version 端点引用 github_client + `settings.GITHUB_URL`）、`GITHUB_URL` 配置常量与默认值、前端 UI 按钮（`AuthPage`、`landing/CTASection`、`landing/Footer`、`AboutDialog` 的 release/github 链接、`ProfileModal` 页脚 GitHub 链接）、README/docs/CONTRIBUTING/SECURITY/CITATION/.github 中的仓库克隆与 issue 链接。
  - **功能（保留，不动）**：GitHub OAuth 登录（`src/infra/auth/oauth.py` 的 `api.github.com` 第三方登录）、Skill 仓库导入（`src/api/routes/github.py` 解析 `github.com/owner/repo` + `GithubImportModal.tsx`）。

## Decisions (locked)

- 英文/技术标识 = `KunXiaoZhi`；用户可见中文显示名 = 「昆小智」。
- `/` 根路由 = **智能跳转**：已登录→`/chat`，未登录→`/auth/login`（复用 token 鉴权判断，与 ProtectedRoute 同源）。
- `/version` 端点 = **简化为纯版本展示**：删除 `src/infra/github_client.py`；`/version` 只返回 `app_version/git_tag/commit_hash/build_time`，移除 `release_url/github_url/has_update/latest_version/published_at` 字段；`VersionResponse` schema 同步瘦身。AboutDialog 删除「检查更新/查看更新/在 GitHub 查看」三按钮及对应区块，只展示当前版本。`useVersion` hook 简化（移除 checkForUpdates/release_url/github_url）。

## Requirements (evolving)

1. 删除 LandingPage 介绍页与整个 `landing/` 目录；`/` 路由重定向到登录页。
2. 全量品牌重塑：所有可见 LambChat → 昆小智/KunXiaoZhi（前端 i18n/manifest/PWA/Tauri/Android/iOS/BrandWordmark/常量；后端配置默认值；README/docs/CONTRIBUTING/SECURITY/CITATION/.github/.env.example/k8s）。
3. 删除全部 GitHub 仓库绑定 UI 按钮 + 前端 `GITHUB_URL` 常量 + 后端 `github_client.py` release 检查 + `version.py` 相关字段 + 配置默认值 + 文档克隆链接。
4. 保留 GitHub OAuth 登录与 Skill 仓库导入功能（仅品牌语境的 github.com 删除）。
5. 测试/lint/typecheck 通过；现有 release/打包测试需同步更新或移除。

## Acceptance Criteria (evolving)

- [ ] 访问 `/` 直接进入登录页（无 LandingPage 介绍页），`landing/` 目录已删除。
- [ ] `grep -ri "lambchat" src/ frontend/src/ docs/ README* .github/ k8s/ deploy/` 在源码与文档中无残留（仅 .trellis 归档历史保留）。
- [ ] 前端无任何 `GITHUB_URL` 引用、无指向 `github.com/Yanyutin753/LambChat` 的 UI 按钮。
- [ ] 后端 `github_client.py` 已删除，`version.py` 不再引用 release/github；`GITHUB_URL` 配置默认值移除。
- [ ] GitHub OAuth 登录与 Skill 导入功能仍正常（测试通过）。
- [ ] `pnpm build` / `tsc --noEmit` / `eslint` / 后端 `ruff`+`mypy`+`pytest` 全绿。
- [ ] PWA manifest/robots/sitemap、Tauri/Android/iOS 配置品牌已更新。

## Definition of Done

- 介绍页删除 + 路由重定向完成
- 全量品牌重塑完成（前端+后端+文档+仓库模板+原生配置）
- GitHub 品牌绑定全删（含 release 检查基础设施）
- 测试同步更新，全量质量门通过
- commit + finish-work

## Out of Scope (explicit)

- GitHub OAuth 登录功能、Skill 仓库导入功能（保留不动）。
- `.trellis/tasks/archive/` 历史归档（历史记录，不改）。
- 第三方依赖自身的 github.com 链接（pre-commit repos、gradle 脚本注释等非品牌内容）。
- 新品牌 Logo/图标美术资源生成（若现有 lamb.webp 等需替换，单独处理或留占位）。
- **文档与仓库模板（本轮明确排除，后续逐步清理）**：README.md、README_CN.md、CONTRIBUTING.md、SECURITY.md、CITATION.cff、CODE_OF_CONDUCT.md、LICENSE 品牌条款、docs/** 全部、.github/ISSUE_TEMPLATE/config.yml。用户决定：本轮专注 web 端 + 后端 + 部署配置清理干净，文档类后续再做。

## Scope actually delivered (本轮实际完成)

- 介绍页删除：删除 `landing/` 目录，`ScrollButtons` 迁移到 `common/`，`/` 路由改为智能跳转（已登录→/chat，未登录→/auth/login）。
- 前端品牌重塑：constants/BrandWordmark/WelcomePage/i18n 五语言/manifest/offline/robots/sitemap/index.html/PWA(pwa.ts/pwaGuards/pwaStatus/sw.ts/main.tsx/PwaStatusToasts)/localStorage 与事件命名空间/下载文件名前缀。
- 前端 GitHub 绑定全删：AuthPage/SessionListContent/SharedPage/ProfileModal 的 GitHub 链接按钮 → 纯文本；ChatInputHelpMenu 文档链接删除；AboutDialog 简化为纯版本展示；GITHUB_URL 常量删除。
- 前端版本链路：VersionInfo/versionApi/useVersion 瘦身，移除 checkForUpdates/release/github 字段。
- 前端原生配置：Tauri(tauri.conf/Cargo.toml/main.rs/lib.rs)、Capacitor、Android(strings.xml/build.gradle + Java 包 com.kunxiaozhi.app 目录迁移)、iOS(Info.plist/project.pbxproj bundle id)、打包脚本(LAMBCHAT_APP_URL→KUNXIAOZHI_APP_URL)。
- 前端 CI：app-release.yml(产物名 KunXiaoZhi- + KUNXIAOZHI_APP_URL)、docs.yml(VitePress base)。
- 后端品牌重塑：config(APP_NAME→昆小智、APP_BASE_URL 示例)、email/share.seo/memory.compaction/tool/scheduler 等可见品牌串；内部命名空间(llm cache key、internal MCP server、arq queue、wecom tmpfile prefix、prompt cache attr、patch attrs)、memory 导出文件名、下载文件名。
- 后端 GitHub 绑定全删：删除 github_client.py、version_utils.py；version.py 简化为纯版本；VersionResponse schema 瘦身；GITHUB_URL 配置默认值移除。
- 部署配置：.env.example、deploy/docker-compose.yml(container_name/image ghcr.io/123zhaozheng/kunxiaozhi)、k8s(文件重命名 kunxiaozhi*.yaml + 内容重塑 namespace/secret/image)。
- 测试同步：前端 8 个测试文件 + 后端 11 个测试文件的品牌串/命名空间断言全部更新。
- 验证：前端 tsc ✅、eslint ✅、改动测试 23/23 ✅；后端 ruff ✅、mypy ✅、pytest 74/76（2 个 Windows CRLF 失败经 stash 验证为 pre-existing）。

## Technical Notes

- 关键文件已查：`App.tsx:58-62,390`、`constants/index.ts`、`BrandWordmark.tsx`、`manifest.json`、`config/base.py:48`、`github_client.py`、`version.py`、`AuthPage.tsx:644,662`、`ProfileModal.tsx:150,165`、`AboutDialog.tsx`、`landing/{CTASection,Footer}.tsx`。
- 待 research：完整逐文件清单（前端/后端/docs/.github/原生/部署）、`version.py` 端点处理方案、图标资源处理、测试影响面。
