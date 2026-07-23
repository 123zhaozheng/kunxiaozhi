# 🤖 昆小智

> 一个可插拔的多租户 AI 智能体平台，由 Skills + MCP 双引擎驱动。

昆小智不只是一个聊天界面，而是一整套可落地的 AI Agent 系统。它把模型管理、MCP 工具接入、技能系统、文件存储、会话分享、人工审批，以及生产可用的前后端基础设施整合进了一个项目里。

## ✨ 核心特性

- **多模型对话**：支持 Claude、GPT、Gemini 等多种模型，实时 SSE 流式输出。
- **角色智能体（Persona）**：可配置的角色预设，自带开场白与头像，支持角色广场浏览与选用。
- **多智能体团队（Team）**：将多个角色编排为协作团队，分工完成复杂任务。
- **技能系统（Skills）**：模块化技能，可从市场安装或从 GitHub 仓库导入，沉淀可复用工作流。
- **MCP 工具集成**：Model Context Protocol 双引擎之一，支持全局与用户级 MCP 服务器，延迟工具加载。
- **记忆系统**：长期记忆与压缩，跨会话保留上下文。
- **企业微信接入**：角色智能体接入企业微信，支持 IM 消息往返与点赞点踩反馈同步。
- **会话分享**：生成公开链接分享会话，带 SEO 与 OG 预览。
- **数据分析看板**：全局使用量、Token 消耗、活跃用户、会话数与各角色智能体指标，支持下钻明细。
- **人工审批**：对敏感操作与工具调用的人工审批流程。
- **多租户与权限**：JWT 认证 + RBAC 细粒度权限控制。
- **多端覆盖**：Web、PWA、桌面端（Tauri）、移动端（Android / iOS）。

## 🏗️ 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI、LangGraph、LangChain、LangSmith、Pydantic v2 |
| 前端 | React、Vite、TypeScript、Tailwind CSS、Recharts、i18next（中/英/日/韩/俄） |
| 智能体 | MCP（langchain-mcp-adapters）、Skills 引擎、多智能体编排 |
| 存储 | MongoDB（会话/事件/追踪）、Redis（缓存/arq 队列） |
| 任务 | arq 异步任务队列 |
| 沙箱 | Daytona / E2B 代码执行沙箱 |
| 多端 | Tauri（桌面）、Capacitor（Android / iOS）、PWA |
| 部署 | Docker、Kubernetes |

## 📦 项目结构

```
.
├── src/                # 后端源码（FastAPI + LangGraph 内核）
│   ├── api/            # 路由层
│   ├── kernel/         # 配置、Schema、内核
│   ├── infra/          # 基础设施（存储/LLM/agent/任务/MCP/技能/分享…）
│   └── agents/         # 智能体定义（核心/fast/search/team）
├── frontend/           # 前端源码（React + Vite）
│   ├── src/
│   ├── src-tauri/      # 桌面端（Tauri）
│   ├── android/        # Android（Capacitor）
│   └── ios/            # iOS（Capacitor）
├── deploy/             # docker-compose 部署
├── k8s/                # Kubernetes 清单
├── Dockerfile          # 多阶段构建（前端 + 后端）
└── pyproject.toml      # Python 依赖（uv 管理）
```

## 🚀 快速开始

### 环境要求

- Python ≥ 3.12（推荐用 [uv](https://github.com/astral-sh/uv) 管理环境）
- Node.js 20 + pnpm
- MongoDB、Redis

### 后端

```bash
# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LLM API Key、MongoDB/Redis 地址等

# 启动
uv run python main.py
```

默认监听 `http://localhost:8000`。

#### 企业微信进程隔离

生产环境建议将 `WECOM_RUNTIME_MODE` 设置为 `external`，并分别启动 Web API
与企业微信 runtime。企微握手超时、重连或 SDK 异常将不会占用 Web API 的事件循环。

```bash
# 终端 1
WECOM_RUNTIME_MODE=external uv run python main.py

# 终端 2
WECOM_RUNTIME_MODE=external uv run python -m src.infra.agent.wecom.runtime
```

也可以运行 `make dev-external`。`embedded` 保留用于兼容现有单进程部署；
`disabled` 完全禁用企微连接。

### 前端

```bash
cd frontend
pnpm install
pnpm dev
```

默认监听 `http://localhost:3001`，访问后会根据登录状态跳转到登录页或会话页。

### Docker 一键启动

```bash
# 启动依赖（Redis + MongoDB）
cd deploy
docker compose up -d

# 构建并运行完整应用
docker build -t kunxiaozhi .
docker run -p 8000:8000 --env-file .env kunxiaozhi
```

### Kubernetes

```bash
# 编辑 k8s/kunxiaozhi-secret.yaml.example 填入密钥后重命名为 kunxiaozhi-secret.yaml
kubectl apply -f k8s/kunxiaozhi-secret.yaml
kubectl apply -f k8s/kunxiaozhi.yaml
```

## 📱 桌面端与移动端打包

```bash
cd frontend

# 生成品牌原生图标资源
pnpm brand:assets

# 桌面端（Tauri）
pnpm package:desktop

# 移动端（Capacitor）
pnpm mobile:build
pnpm mobile:sync
```

## ⚙️ 配置

所有配置项见 `.env.example`，主要包含：

- **应用**：`APP_NAME`、`APP_BASE_URL`、`HOST`/`PORT`、`LOG_LEVEL`
- **LLM**：模型 API Key、重试与 Prompt 缓存策略
- **数据库**：`MONGODB_URL`、`REDIS_URL`
- **任务**：`TASK_BACKEND`（local / arq）、arq 队列配置
- **MCP**：全局/用户级 MCP 服务器缓存与并发
- **沙箱**：Daytona / E2B 配置
- **认证**：JWT 密钥、OAuth（GitHub 等）登录
- **追踪**：LangSmith tracing 开关

## 📄 许可证

详见 [LICENSE](LICENSE)。
