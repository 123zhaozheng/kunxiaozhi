# Implement — Agent emoji CDN 本地化

> Active task: `.trellis/tasks/07-16-agent-emoji-cdn-local`

## 执行清单

1. **审计引用**  
   `rg "FluentEmoji|getFluentEmojiCDN" frontend/src`  
   预期命中：`DynamicIcon.tsx`、`SubagentBlocks.tsx`（及测试）。

2. **扩展 `personaAvatar.ts`**  
   - 导出 `emojiToCodepoints`（若测试需要）或保持 private。  
   - 新增 `getEmojiAvatarSrcCandidates(emoji): string[]`（exact + fe0f 变体）。  
   - 保持 `getEmojiAvatarUrl` 返回第一候选（exact）。

3. **新增 `LocalFluentEmoji` 组件**（`frontend/src/components/common/LocalFluentEmoji.tsx`）  
   - props: `emoji`, `size`, `className`  
   - img + onError 切换候选；全失败则 span 显示原始 emoji 字符。  
   - 固定宽高 box，对齐现 DynamicIcon 布局。

4. **改 `DynamicIcon.tsx`**  
   - 删除 `FluentEmoji` import。  
   - 使用 `LocalFluentEmoji`。  
   - 更新 `DynamicIcon.test.ts`。

5. **改 `SubagentBlocks.tsx`**  
   - 删除 `FluentEmoji` import。  
   - emoji 分支用 `LocalFluentEmoji`。  
   - 如有相关 test 更新断言。

6. **allowlist + 资源**  
   - 检查并补 `emoji-allowlist.json`（AgentIconSelect + ⭐💬🤖 等）。  
   - 有网：`cd frontend; node scripts/fetch-emoji-assets.mjs`  
   - 无网：仅补能从现有 public 复制/已有的文件；404 项记 notes。  
   - 确认 `1f916.webp`、`1f4ac.webp` 等关键文件存在。

7. **验证**  
   - `rg "FluentEmoji|getFluentEmojiCDN|npmmirror" frontend/src` → 业务源码无命中。  
   - 跑相关 node:test。  
   - （可选）`pnpm run build` 检查 dist。

## 验证命令

```powershell
cd frontend
rg "FluentEmoji|getFluentEmojiCDN" src
node --test src/components/common/__tests__/DynamicIcon.test.ts src/components/agent/__tests__/AgentIcon.test.ts
# 有网时
node scripts/fetch-emoji-assets.mjs
```

## 回滚点

- 恢复 DynamicIcon / SubagentBlocks 的 FluentEmoji 用法。  
- 删除 LocalFluentEmoji。  
- allowlist/资源增量可留可删。

## 注意

- 不允许 git commit（sub-agent 规范）；主会话按用户要求提交。  
- 不改 Lucide / model SVG。  
- 外科手术式改动，不顺手重构。
