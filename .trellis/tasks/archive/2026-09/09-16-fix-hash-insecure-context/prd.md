# 修复内网 HTTP 环境上传哈希崩溃（crypto.subtle 安全上下文兜底）

## 背景

内网部署通过明文 HTTP（`http://<节点IP>:30080`）访问，上传文档时前端在
`hashWorker.ts` 用 `crypto.subtle.digest("SHA-256")` 计算文件哈希。Web Crypto 的
`crypto.subtle` 仅存在于安全上下文（HTTPS 或 localhost），HTTP 下为 `undefined`，
读取 `.digest` 抛出 `Cannot read properties of undefined (reading 'digest')`，
导致内网上传功能整体不可用（配额预留、blob 写入均无法到达）。

该代码由 2026-03-26 的 e28ba124 + 64ac6629 引入，此前仅在 HTTPS/localhost 环境
验证过，属潜伏缺陷而非近期回归。

## 需求

- `hashWorker.ts`：`crypto.subtle` 可用时走原生路径（行为零变化）；不可用时
  回退到纯 JS SHA-256 实现（内嵌模块，无新依赖，不影响离线构建）。
- 纯 JS 实现独立成模块（`frontend/src/workers/sha256.ts`），便于在 Node 测试中
  与 `node:crypto` 对照验证。
- Worker 对外消息协议（`{file}` 入、`{hash}` / `{error}` 出）不变，
  `useFileUpload.ts` 不改。

## 验收标准

1. 内网 HTTP 环境上传文档不再报 `reading 'digest'` 错误，哈希结果与原生实现逐位一致
2. 新增单测：纯 JS SHA-256 输出与 `node:crypto` 对照（空串、"abc"、多块数据、大缓冲）全部一致
3. `tsc -b`、`eslint`、相关测试通过
4. 安全上下文路径不受影响（仍走 `crypto.subtle`）

## 非目标

- 不做内网 HTTPS 改造
- 不改后端 checkFile/upload 契约
- 不优化 JS 哈希性能（Worker 内执行，页面不卡即可）
