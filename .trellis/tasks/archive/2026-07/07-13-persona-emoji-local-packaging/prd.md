# 内网 persona emoji 图标本地化

## 背景

内网隔离环境无法访问外网 CDN。persona 头像中的 emoji 类型（`isEmojiAvatar`）通过 `@lobehub/fluent-emoji` 的 `getFluentEmojiCDN` 生成 URL，默认指向 `https://registry.npmmirror.com/@lobehub/fluent-emoji-anim-N/latest/files/assets/{codepoints}.webp`，内网全部 404，头像失效。

## Goal

将 fluent-emoji anim 资源本地打包进前端构建产物，emoji 头像在内网零外网依赖下正常渲染。

## Requirements

- 把 `@lobehub/fluent-emoji-anim-1..4` 四个包的 `assets/*.webp` 下载并扁平化到前端本地静态目录（codepoint 在包间唯一，可安全 flatten）。
- 改写 `getEmojiAvatarUrl`：不再调 `getFluentEmojiCDN`，改为计算本地路径 `/emoji-assets/{codepoints}.webp`（codepoint 转换逻辑与包内 `emojiToUnicode` 等价）。
- 资源随前端构建产物一起部署，内网运行时无任何外网请求。
- 提供可复现的资源打包脚本（开发机有网时执行一次，产物提交进仓库或构建流程）。

## 范围外

- 不改 `icon:` 前缀的 persona 图标（那是另一套 SVG/组件方案，非 CDN 依赖）。
- 不改非 emoji 的图片头像（`http`/`/` 开头）。
- 不做 3d/flat/modern/mono 变体本地化（persona 头像只用 `type: "anim"`）。

## Acceptance Criteria

- [ ] `frontend/public/emoji-assets/` 下含 anim 全量 webp（或覆盖 persona 实际使用的 emoji 集合，且文档说明）。
- [ ] `getEmojiAvatarUrl('😀')` 返回形如 `/emoji-assets/1f600.webp` 的本地路径，不含任何外网域名。
- [ ] 构建产物 `dist/` 下存在 `emoji-assets/`，静态服务可访问。
- [ ] 打包脚本可重复执行（幂等），有说明文档/注释。
- [ ] 现有 emoji 头像在本地 dev 与构建后均能渲染（验证至少几个常见 emoji codepoint 文件存在）。
- [ ] `npm run build` 通过，无外网依赖残留。

## Notes

- CDN 机制（已读包源码 `es/getFluentEmojiCDN/index.js` + `utils.js`）：
  - `emojiToUnicode(emoji)` = `[...emoji].map(c => c.codePointAt(0).toString(16)).join('-')`
  - `type: "anim"` → `{ pkg: emojiAnimPkg(emoji), path: "assets/" + codepoints + ".webp" }`
  - `emojiAnimPkg` 按 codepoint 首段范围分 4 包：`<1f469`→anim-1、`1f469..1f620`→anim-2、`1f620..1f9a0`→anim-3、`>=1f9a0`→anim-4
  - 默认 cdn `aliyun` → `registry.npmmirror.com/{pkg}/latest/files/{path}`
- 修改文件：`frontend/src/components/persona/personaAvatar.ts`（`getEmojiAvatarUrl` 第 77-79 行、import 第 1 行）。
- 资源下载源（开发机有网）：`https://registry.npmmirror.com/@lobehub/fluent-emoji-anim-N/latest/files/assets/` 或 `npm pack @lobehub/fluent-emoji-anim-N` 取 tarball。
- persona 头像为运行时 DB 配置，emoji 集合不固定 → 默认打包全量 anim 资源以保证任意 emoji 可用。
