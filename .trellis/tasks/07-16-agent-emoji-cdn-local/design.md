# Design — Agent emoji CDN 本地化

## 问题分解

| 路径 | 现状 | 目标 |
|------|------|------|
| Persona 头像 emoji | `getEmojiAvatarUrl` → 本地 anim | 保持 |
| `DynamicIcon`（AgentIcon 默认/emoji） | `FluentEmoji type="3d"` → CDN | 本地 img |
| Subagent 默认 emoji | `FluentEmoji type="3d"` → CDN | 本地 img |
| Lucide 角色图标 | 本地矢量 | 不动 |
| Model lobe SVG | 本地 static-svg | 不动 |

## 方案

### 1. 统一 emoji 渲染 helper

在 `personaAvatar.ts`（或紧邻的小模块）扩展：

```ts
export function getEmojiAvatarUrl(emoji: string): string {
  return `/emoji-assets/${emojiToCodepoints(emoji)}.webp`;
}

/** Prefer exact codepoints; if missing on disk we cannot know at build time,
 *  so runtime uses a small candidate list for FE0F variants. */
export function getEmojiAvatarCandidates(emoji: string): string[] {
  const base = emojiToCodepoints(emoji);
  const candidates = [base];
  if (!base.endsWith("-fe0f")) candidates.push(`${base}-fe0f`);
  else candidates.push(base.replace(/-fe0f$/, ""));
  // also strip all fe0f segments for multi-codepoint
  const stripped = base.split("-").filter((p) => p !== "fe0f").join("-");
  if (stripped && stripped !== base) candidates.push(stripped);
  return [...new Set(candidates)].map((cp) => `/emoji-assets/${cp}.webp`);
}
```

运行时组件用 **首个候选 + onError 切下一个**，或简单实现：只尝试 exact + `+fe0f`（img onError 回退到下一个 src / 最终 fallback 文本 emoji）。

**推荐最小实现（避免复杂 state）：**

- `LocalFluentEmoji` 小组件：`src = getEmojiAvatarUrl(emoji)`，`onError` 时若未试过 fe0f 则改 src，再失败显示 unicode 文本（至少不空白）。

放在 `frontend/src/components/common/LocalFluentEmoji.tsx`（或直接内联在 DynamicIcon）。

### 2. DynamicIcon

```tsx
// 不再 import FluentEmoji
export function DynamicIcon({ name, size, className }) {
  const emoji = !name || LEGACY_DEFAULT_ICONS.has(name) ? "💬" : isAsciiName(name) ? "💬" : name;
  return <LocalFluentEmoji emoji={emoji} size={size} className={className} />;
}
```

ASCII legacy（`Bot`、`Brain`、`Settings` 等）→ 💬，与现逻辑一致（现也是 fallback 💬）。  
注意：`AgentIcon` 已对 default Bot 转成 🤖，故 Agent 列表默认是 🤖 本地图。

### 3. SubagentBlocks

`roleIconMeta.emoji` 分支：

```tsx
{roleIconMeta.emoji ? (
  <LocalFluentEmoji emoji={roleIconMeta.emoji} size={22} />
) : (
  <RoleIcon ... />
)}
```

### 4. 资源 allowlist

更新 `frontend/scripts/emoji-allowlist.json`，确保至少包含：

- AgentIconSelect 列表：✨ 🤖 🎓 💻 ✍️ 🛡️ 📊 ⚡ 📦 🎨 🎵 📚 🧠 🔬 💬 🌟
- Subagent：⭐ 🤖
- DynamicIcon 默认：💬

对 anim 包中不存在的 codepoint（脚本 404），从 allowlist 去掉或换成存在的近义 emoji；`getEmojiAvatarUrl` 的 onError 兜底 unicode。

有网时执行：

```powershell
cd frontend; node scripts/fetch-emoji-assets.mjs
```

仓库提交更新后的 webp（或依赖 Docker build 阶段 fetch——**内网 Docker 若无 mirror 会失败**；当前 Dockerfile 假定 builder 有 mirror。本地 `public/emoji-assets` 已提交的应继续提交增量文件）。

### 5. 测试

- 更新 `DynamicIcon.test.ts`：markup 含 `/emoji-assets/` 且不含 `npmmirror`。
- 更新 `AgentIcon.test.ts`：不要求 Fluent 3d 文案；断言默认走 🤖 或本地路径约定。
- 可选：`personaAvatar` 单测 candidates/FE0F。

### 6. 数据流

```
agent.icon = "Bot" | "🤖" | "✨" | lobe-slug
  → AgentIcon
      lobe slug? → ModelIconImg (local SVG)
      else → DynamicIcon
               → LocalFluentEmoji
                    → /emoji-assets/{cp}.webp (同源静态)
                    → onError → fe0f 变体 → unicode fallback
```

## 兼容 / 回滚

- 纯前端；回滚恢复 `FluentEmoji` import。
- 视觉：3d → anim，可接受。
- 不删 `@lobehub/fluent-emoji` 依赖除非确认无间接需要（`@lobehub/ui` peer）；本任务以去掉业务 import 为准，不强行 uninstall。

## 风险

| 风险 | 缓解 |
|------|------|
| anim 包缺某些 emoji | allowlist 实测；onError unicode 兜底 |
| 动画 WebP 在弱机卡顿 | 可接受；后续可换静图 |
| Docker build 无网且 public 未提交新文件 | 提交新增 webp；或文档要求 builder 有 mirror |
