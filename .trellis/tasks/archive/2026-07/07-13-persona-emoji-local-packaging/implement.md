# Implement — persona emoji 本地化

> Active task: `.trellis/tasks/07-13-persona-emoji-local-packaging`

## 执行清单

1. **grep 确认引用面**：`grep -rn "fluent-emoji\|getFluentEmojiCDN\|getEmojiAvatarUrl" frontend/src` → 记录所有引用点，确认改动面。

2. **写打包脚本** `frontend/scripts/fetch-emoji-assets.mjs`：
   - 对 N=1..4 调 `npm pack @lobehub/fluent-emoji-anim-N`（或直接 fetch npmmirror tarball）。
   - 解压取 `package/assets/*.webp`，flatten 到 `frontend/public/emoji-assets/`。
   - 幂等：先清空目标目录。
   - → 验证：脚本跑完 `public/emoji-assets/` 有 webp 文件。

3. **执行打包脚本**（开发机有网）下载资源 → 验证：抽查 `1f600.webp`、`1f680.webp` 等存在且非空。

4. **改 `personaAvatar.ts`**：
   - 删 `import { getFluentEmojiCDN } from "@lobehub/fluent-emoji"`。
   - 加 `emojiToCodepoints` + 重写 `getEmojiAvatarUrl` 为本地路径。
   - → 验证：`getEmojiAvatarUrl('😀')` === `'/emoji-assets/1f600.webp'`。

5. **依赖清理**：若 grep 确认全项目仅此处用 `@lobehub/fluent-emoji`，从 `package.json` 移除（`npm uninstall`）；否则保留。**先 grep 再决定**。

6. **构建验证**：`npm run build` 通过；检查 `dist/emoji-assets/` 存在且含 webp。

## 验证命令

```powershell
# 引用面
cd frontend; grep -rn "fluent-emoji\|getFluentEmojiCDN\|getEmojiAvatarUrl" src

# 打包脚本
cd frontend; node scripts/fetch-emoji-assets.mjs

# 资源抽查
ls frontend/public/emoji-assets/1f600.webp frontend/public/emoji-assets/1f680.webp

# 构建
cd frontend; npm run build

# 构建产物
ls dist/emoji-assets/ | head
```

## 注意

- 内网 Docker build 无互联网 → 资源**必须**随仓库提交（`public/emoji-assets/` 不能被 `.gitignore`）。
- `emojiToCodepoints` 必须用 `Array.from`（按 code point 展开），与包内 spread 一致；flag/astral emoji 才正确。
- 路径用绝对 `/emoji-assets/...`（同源根路径），dev/build/静态托管一致。
- 不要改 `icon:` 前缀头像与非 emoji 图片头像逻辑。
- 若资源文件数量过多影响仓库，先与用户确认是否接受全量提交（默认接受，因 persona emoji 运行时可变）。
- 不允许 git commit（sub-agent 规范）。
