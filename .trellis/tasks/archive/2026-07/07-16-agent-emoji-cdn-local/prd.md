# Agent / DynamicIcon FluentEmoji CDN 本地化

## 背景

内网 Chrome 109 环境无法访问外网。上一轮 `07-13-persona-emoji-local-packaging` 已把 **persona emoji 头像** 改成同源 `/emoji-assets/{codepoints}.webp`，并在 Docker build 中 `fetch-emoji-assets.mjs` 打包 anim 资源。

但 agent 选择器（Fast / Search 等）前的小图标仍走：

```
AgentIcon → DynamicIcon → <FluentEmoji type="3d" />
  → getFluentEmojiCDN → https://registry.npmmirror.com/@lobehub/fluent-emoji-3d/...
```

用户在 Network 中已确认存在对 `registry.npmmirror.com` 的请求；内网失败后图标空白。Subagent 默认 🤖/⭐ 同样直连 CDN。

Chrome 109 本身可正确解码本地彩色 WebP（含动画）；问题是 **CDN 不可达 + 部分 codepoint 本地文件缺失/FE0F 不一致**，不是浏览器不支持彩色。

## Goal

消除前端运行时对 `registry.npmmirror.com` / `@lobehub/fluent-emoji-*` CDN 的依赖：所有 emoji 图标统一走已本地化的 `/emoji-assets/` anim 资源，agent / DynamicIcon / Subagent 图标在内网可显示。

## Requirements

1. **去掉 CDN 渲染路径**
   - `DynamicIcon` 不再使用 `<FluentEmoji type="3d" />`，改为本地 `img`（`getEmojiAvatarUrl`）。
   - `SubagentBlocks` 中对 `FluentEmoji` 的直接调用改为本地 `img`。
   - 全 `frontend/src` 源码中无任何运行时 `FluentEmoji` / `getFluentEmojiCDN` 调用（依赖包可保留若被 `@lobehub/ui` 间接依赖，但业务代码不得再 import 用于渲染）。

2. **路径与兼容**
   - 继续复用 `getEmojiAvatarUrl` → `/emoji-assets/{codepoints}.webp`。
   - 增加 **FE0F 变体回退**：例如 codepoint `1f6e1` 不存在时尝试 `1f6e1-fe0f`（及反向可选），减少 allowlist/文件名不一致导致的 404。
   - 未识别的 ASCII legacy 名（`Bot` / `MessageCircle` / `Brain` 等）映射到默认 💬 或 🤖 本地资源，行为与现 `DynamicIcon` 一致。

3. **资源覆盖**
   - 确保 agent 图标选择器（`AgentIconSelect`）与默认 bot 图标（🤖）、subagent 默认（⭐/🤖）在 `emoji-allowlist.json` 中存在且本地文件可解析。
   - 缺失项（如 ✨ `2728`、⚡ `26a1`、🛡️ 裸 `1f6e1`、⭐ `2b50` 等）补进 allowlist；有网环境执行 `node scripts/fetch-emoji-assets.mjs` 更新 `public/emoji-assets/`。
   - 若某个 emoji 在 anim 包中不存在（脚本 404），在 PRD/实现备注中记录并用相邻可用 emoji 或保留系统 emoji 兜底策略（优先本地 webp）。

4. **部署**
   - Docker `frontend-builder` 已有 `RUN node scripts/fetch-emoji-assets.mjs`；保持可用。
   - 构建产物 `dist/emoji-assets/` 必须含修复后的资源；运行时 Network 无 `npmmirror` / `fluent-emoji-3d` 请求。

## 范围外

- 不引入完整 `fluent-emoji-3d` 包本地化（体积大）；统一用已有 **anim** 资源即可。
- 不改 Lucide 单色矢量图标（角色 subagent 的 Search/Code 等）——它们本就是单色 UI，不是 CDN 问题。
- 不改 lobe model SVG 图标链路（`@lobehub/icons-static-svg` 已本地打包）。
- 不升级/更换内网 Chrome 109。
- 不做 emoji 动画性能优化（可选后续）。

## Acceptance Criteria

- [x] `frontend/src` 业务代码中无 `FluentEmoji` / `getFluentEmojiCDN` import 与使用。
- [x] `DynamicIcon` / `AgentIcon` 渲染结果中 `img[src]` 为 `/emoji-assets/...webp`，不含外网域名。
- [x] 默认 agent 图标（Bot → 🤖）在本地资源存在且可访问。
- [x] Subagent 默认 emoji 图标走本地路径。
- [x] `getEmojiAvatarUrl` 对 FE0F 变体有合理回退（或 allowlist 补齐后文件名一致）。
- [x] allowlist 覆盖 AgentIconSelect 常用 emoji；fetch 脚本可成功拉取新增项（有网时）。
- [x] 相关单元测试更新并通过（`DynamicIcon` / `AgentIcon` 等）。
- [x] 文档化：内网验证步骤——Network 无 `registry.npmmirror.com`。

## Notes

- 已归档前序任务：`archive/2026-07/07-13-persona-emoji-local-packaging`（persona 路径本地化）。
- 用户已确认线上 Network 有 `registry.npmmirror.com` 请求。
- 视觉从 3d 变为 anim 动图/静图，可接受；目标是内网可用而非像素级一致。
