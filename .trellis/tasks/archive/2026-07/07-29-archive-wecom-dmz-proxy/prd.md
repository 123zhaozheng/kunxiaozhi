# 归档企业微信 DMZ 代理部署

## Goal

将本次企业微信网络联调采用的 HTTP CONNECT 正向代理部署样例纳入版本控制，完成独立提交并归档任务，供后续内网部署复用。

## Background

- `deploy/wecom-dmz-proxy/` 是无需自定义 CA 的 Squid HTTP CONNECT 正向代理；当前容器健康运行并已通过 LambChat 企业微信 WebSocket 探测。
- 最终部署方案已确认为正向代理；反向 Nginx 样例和测试证书不进入本次归档提交。
- `deploy/wecom-dmz-proxy/` 目前尚未纳入 Git。

## Requirements

- R1：归档 `deploy/wecom-dmz-proxy/` 的 Dockerfile、Compose、Squid 白名单配置和 README。
- R2：验证 Compose 文件可解析且 Squid 配置语法有效。
- R3：验证正向代理仍可由 LambChat 在不配置自定义 CA 时完成企业微信 WSS 探测，并拒绝非白名单目标。
- R4：反向 Nginx 目录、测试证书和私钥不进入提交。
- R5：只提交本任务文件，不混入工作区现有 Persona 功能改动。
- R6：完成独立 Git 提交后归档本 Trellis 任务。

## Acceptance Criteria

- [x] AC1：Git 暂存内容包含正向代理目录的四个可复用源文件，不包含反向 Nginx 或任何密钥、证书。
- [x] AC2：`docker compose config` 和 `squid -k parse` 均通过。
- [x] AC3：LambChat 通过 `http://localhost:3128` 完成企业微信 WSS 探测，且 CA 路径为空。
- [x] AC4：对非企业微信目标的 CONNECT 请求被代理拒绝。
- [x] AC5：提交仅包含本任务目录及 `deploy/wecom-dmz-proxy/`，其他工作区改动保持未暂存。
- [x] AC6：任务归档完成，并在开发者日志中记录提交。

## Out of Scope

- 不修改企业微信网络设置功能本身。
- 不归档或启用反向 Nginx 部署样例。
- 不为媒体下载补充额外域名白名单。
