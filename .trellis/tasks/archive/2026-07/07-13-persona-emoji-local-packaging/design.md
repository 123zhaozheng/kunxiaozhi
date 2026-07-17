# Design — persona emoji 本地化

## 决策：本地静态目录 + 路径改写

### 资源放置

`frontend/public/emoji-assets/{codepoints}.webp`

- `public/` 在 Vite dev 与 build 后均映射到根路径 → 访问 URL `/emoji-assets/{codepoints}.webp`。
- 扁平化（不保留 anim-1/2/3/4 子目录）：codepoint 在四包间唯一（包按范围划分、互斥），flatten 无冲突，URL 更简单。
- 后端 FastAPI 静态服务托管 `dist/` 整个目录，`/emoji-assets/*` 随之可用（无需额外路由）。

### `getEmojiAvatarUrl` 改写

`personaAvatar.ts`：

```ts
// 删除：import { getFluentEmojiCDN } from "@lobehub/fluent-emoji";

function emojiToCodepoints(emoji: string): string {
  return Array.from(emoji)
    .map((ch) => ch.codePointAt(0)!.toString(16))
    .join("-");
}

export function getEmojiAvatarUrl(emoji: string): string {
  return `/emoji-assets/${emojiToCodepoints(emoji)}.webp`;
}
```

- 与包内 `emojiToUnicode`（`_toConsumableArray` ≈ `Array.from` 按 code point 展开）等价，astral/flag emoji 正确处理。
- `Array.from(str)` 与包的 spread 都按 Unicode code point 迭代，行为一致。

### 资源获取（打包脚本）

新增 `frontend/scripts/fetch-emoji-assets.mjs`（或 `.ps1`），开发机有网时执行：

1. 对 N=1..4：从 `https://registry.npmmirror.com/@lobehub/fluent-emoji-anim-N/latest/files/assets/` 拉取目录，或 `npm pack` 取 tarball 解压 `package/assets/*.webp`。
2. flatten 拷贝到 `frontend/public/emoji-assets/`。
3. 幂等：每次清空目标目录再写入。

推荐 `npm pack` 方案（tarball 稳定、可校验）：下载 4 个 tgz → 解压 → 取 `package/assets/*.webp` → 拷贝。

### 资源提交策略

- 全量 anim webp ≈ 数百~千余文件。提交进仓库以保证内网构建零外网依赖（内网 Docker build 无互联网）。
- 在 `.gitignore` 中**不要**忽略 `frontend/public/emoji-assets/`。
- 脚本仅在资源更新时手动跑一次；常规构建不再依赖网络。

### `@lobehub/fluent-emoji` 依赖去留

- 改写后 `personaAvatar.ts` 不再 import 该包。
- 检查是否有其他文件引用（grep `fluent-emoji` / `getFluentEmojiCDN`）；若全项目仅此处用，可从 `package.json` 移除依赖（减小体积）；否则保留。**subagent 需 grep 确认**，不要盲目删。

## 数据流

```
persona avatar (emoji 字符, e.g. "🚀")
  -> emojiToCodepoints("🚀") = "1f680"
  -> "/emoji-assets/1f680.webp"
  -> 浏览器请求同源静态资源 (Vite dev / 构建后 dist / FastAPI 静态托管)
  -> 渲染头像
```

## 兼容性 / 回滚

- 纯前端改动 + 新增静态资源，不影响后端。
- 回滚：恢复 `getFluentEmojiCDN` import 与 `getEmojiAvatarUrl` 原实现，删除 `public/emoji-assets/`。
- dev / build / 静态托管三处路径一致，无环境差异。
