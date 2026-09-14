# 本地预览与依赖启动研究

> 研究时间：2026-09-14 UTC
> 研究范围：只读检查仓库、运行时工具、端口与依赖解析；没有修改业务代码、配置文件或锁文件。

## 1. 结论摘要

- 请求的仓库已经位于 `/tmp/hoplite/workspace/kunxiaozhi`，嵌套 Git 仓库的 `origin` 是 `https://github.com/123zhaozheng/kunxiaozhi.git`，当前为 `main`，HEAD `bf89b3e`。
- 当前沙箱没有 Docker、`redis-server`、`redis-cli`、`mongod`、`mongosh`，6379/27017/8000/3001 均没有监听进程；因此不能直接用仓库内的 Docker Compose 方案或立即启动完整应用。
- 项目实际需要 **MongoDB + Redis 两个可连接的 TCP 服务**：MongoDB 在应用 lifespan 中用于 settings、用户/角色和多组索引初始化；Redis 在运行时用于 pub/sub、WebSocket、任务与 arq。只把 `TASK_BACKEND` 改成 `local` 不能省掉 Redis，因为 pub/sub listeners 仍会启动。
- 当前 Hoplite 项目设置没有仓库 `.hoplite/settings.json`，但有外部 run override：
  `python3 -m http.server "$HOPLITE_PREVIEW_PORT"`。因此直接调用 managed Preview 只会启动静态 HTTP 文件服务器，不会启动本项目的后端或 Vite 前端；这是交付可点击自测前必须处理的 Preview 阻塞点。
- 推荐的正常本地流程是：准备真实/临时 MongoDB 与 Redis → `uv sync --frozen` → `cd frontend && pnpm install --frozen-lockfile` → 后端 8000 + Vite 3001 → `GET /health` 与 `GET /ready` 验证 → 浏览器注册第一个用户。仓库没有 demo 账号或 seed 脚本。

## 2. 项目启动方式

### 后端

文档、Makefile 与入口代码一致：

```bash
cd /tmp/hoplite/workspace/kunxiaozhi
uv sync --frozen
cp .env.example .env
uv run python main.py
# 等价：make dev
```

- `main.py` 调用 `uvicorn`，默认 `HOST=0.0.0.0`、`PORT=8000`。
- Python 要求 `>=3.12`；本沙箱是 Python 3.12.3，`uv` 是 0.9.28。
- 从仓库根目录启动很重要：默认 `LOCAL_STORAGE_PATH=./uploads` 是相对路径，应用启动时会创建 `uploads/`、`uploads/revealed_files/` 与 `uploads/revealed_projects/`。
- 企业微信不需要为本地自测启动：默认 `WECOM_RUNTIME_MODE=embedded`，也可以明确使用 `WECOM_RUNTIME_MODE=disabled` 或 `external` 避免无关网络连接。

### 前端

```bash
cd /tmp/hoplite/workspace/kunxiaozhi/frontend
pnpm install --frozen-lockfile
pnpm dev
```

- Vite 默认监听 `0.0.0.0:3001`。
- `frontend/vite.config.ts` 将 `/api`、`/tools`、`/human`、`/health`、`/ws`、`/services` 以及已列出的 agent 路由代理到 `http://127.0.0.1:8000`。
- 前端源码实际读取的是 `VITE_API_BASE`（`frontend/src/services/api/config.ts`），不是 `frontend/.env.example` 里的 `VITE_API_URL`。本地开发时最简单的做法是不要设置 `VITE_API_BASE`，让请求使用相对路径并走 Vite proxy；若要绕过代理，设置 `VITE_API_BASE=http://127.0.0.1:8000`。
- `make dev-all` 会以 `FRONTEND_DEV_URL=http://127.0.0.1:3001` 启动后端并同时启动 Vite，是仓库提供的前后端开发快捷命令。

### 生产静态前端

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
uv run python main.py
```

后端会优先寻找 `static/`，其次寻找 `frontend/dist/`。Dockerfile 的多阶段构建会把 `frontend/dist` 拷贝到 `/app/static`。如果不构建前端且不设置 `FRONTEND_DEV_URL`，后端本身不能提供可用的 SPA 页面；开发时应使用独立 Vite 服务和 `FRONTEND_DEV_URL`。

## 3. 关键环境变量与应用依赖

建议以根目录 `.env.example` 为模板，不要使用 `deploy/.env.example`（后者只有极少的模型注释）。本地最小相关配置如下，密码/密钥仅用于说明，不应提交：

```dotenv
HOST=0.0.0.0
PORT=8000
REDIS_URL=redis://127.0.0.1:6379/0
MONGODB_URL=mongodb://127.0.0.1:27017
MONGODB_DB=agent_state
MONGODB_USERNAME=
MONGODB_PASSWORD=
MONGODB_AUTH_SOURCE=admin
S3_ENABLED=false
LOCAL_STORAGE_PATH=./uploads
ENABLE_LOCAL_FILESYSTEM_FALLBACK=true
ENABLE_REGISTRATION=true
REQUIRE_EMAIL_VERIFICATION=false
TURNSTILE_ENABLED=false
WECOM_RUNTIME_MODE=disabled
# 可选：TASK_BACKEND=local；默认值是 arq，且 ARQ_EMBEDDED_WORKER 默认 true
```

要点：

1. `.env.example` 的 Mongo 默认是 `mongodb://localhost:27017`，Redis 默认是 `redis://localhost:6379/0`，均无认证。
2. `S3_ENABLED=false` 时会选择本地文件后端；上传内容落在 `LOCAL_STORAGE_PATH`。这适合当前自测，但不代表生产存储容量有保护，用户存储管理功能后续仍需围绕该路径/S3 统一统计和清理。
3. `TASK_BACKEND` 在 `Settings` 默认是 `arq`，`ARQ_EMBEDDED_WORKER` 默认是 `true`；`.env.example` 没有显式写出这两项。设置 `TASK_BACKEND=local` 可减少 arq worker 复杂度，但不能移除 Redis：`start_runtime_services()` 仍会启动 task/pubsub、settings、model、tool、MCP、WebSocket 等 Redis listeners。
4. JWT 空值可以由代码自动生成，临时自测可以工作；如果要重启后保留浏览器会话，应该提供稳定的 `JWT_SECRET_KEY`。
5. `.env.example` 没有 LLM provider key；README 说明模型可以在部署后通过 Model Config UI 配置。因此启动/注册自测与实际聊天是两个层次：没有配置模型时不要把“页面能登录”当作“Agent 能回答”。本项没有在缺失依赖的沙箱中启动验证。

## 4. 启动时为何必须有 MongoDB/Redis

### MongoDB

`src/api/main.py` 的 lifespan 在接受请求前会：

- 先通过 `SettingsService` 从 MongoDB 读取/导入数据库设置（数据库优先于环境变量）；
- 并行初始化 agent config、model、skill、trace、session、revealed file、notification、analytics、WeCom binding 等存储索引；
- `/ready` 还会 fail-closed 地检查 trace storage 的 MongoDB 索引是否 ready。

因此 MongoDB 不只是用户注册时才用。即使只访问登录页，也建议先确认 `/ready` 返回 200；MongoDB 不可达时预期是 lifespan/索引初始化报错或 readiness 503，而不是可用的完整后端。

### Redis

`start_runtime_services()` 会启动任务管理 pub/sub、arq（默认）、设置/模型/tool/MCP cache pub/sub、WebSocket 等监听器；这些使用 `REDIS_URL`。没有 Redis 时应用可能在 lifespan 的 runtime listener 阶段失败或反复重连，不能作为完整预览依赖。

### 健康检查

```bash
curl -f http://127.0.0.1:8000/health
curl -f http://127.0.0.1:8000/ready
```

- `/health` 返回内存监控和版本信息，主要是存活检查，不等价于 Mongo/Redis 全链路健康。
- `/ready` 返回 `{"status":"ready"}` 才表示 trace Mongo 索引预检通过；失败时是 503，响应含 `not_ready` 和索引状态。
- K8s 清单中的参考探针是 `/health`（初始延迟 120 秒）和 `/ready`（初始延迟 60 秒），反映了首次索引/启动可能较慢。

## 5. 当前沙箱的验证证据

已执行的只读检查结果：

| 检查 | 结果 |
| --- | --- |
| Python / Node / pnpm / uv | Python 3.12.3 / Node 24.19.0 / pnpm 10.32.1 / uv 0.9.28 |
| Python 运行依赖 | 系统 Python 没有 `fastapi`、`uvicorn`、`redis`、`pymongo`、`motor`、`pytest`、`httpx`；仓库 `.venv` 不存在 |
| 前端依赖 | `frontend/node_modules` 不存在；`pnpm install --frozen-lockfile --offline --ignore-scripts --lockfile-only` 成功解析锁文件，但没有安装依赖 |
| Python 锁文件 | `uv sync --dry-run --frozen` 成功解析，预计安装/下载 203 个包并创建 `.venv` |
| Docker | `docker` 不存在，`docker version` 报 `command not found` |
| Redis/Mongo 可执行文件 | `redis-server`、`redis-cli`、`mongod`、`mongosh`、`mongo` 均不存在 |
| 端口 | 6379、27017、8000、3001 均关闭；`curl` 对 8000/3001 返回连接失败 |
| apt 包 | 当前 apt 元数据中 `redis-server` 与 `mongodb` 都是 `Unable to locate package`；不能据此假设 apt 安装路径可用 |
| Homebrew | 沙箱以 root 运行，Homebrew 拒绝执行：`Running Homebrew as root is extremely dangerous and no longer supported` |
| 仓库内替代服务 | 未找到 `mongomock`、`fakeredis`、`testcontainers` 或 `mongodb-memory-server` 依赖 |
| LLM/账号 seed | 没有 demo/fixture/seed 初始化脚本；没有提交的演示账号 |

`uv sync --dry-run` 解析到的锁定版本包含 `fastapi`、`uvicorn`、`motor`、`pymongo`、`redis`、`arq` 等，依赖安装本身是可重复的；前端实际安装建议使用 `pnpm install --frozen-lockfile`。

## 6. 无 Docker 的服务方案

### 首选：连接外部/托管服务

如果当前平台允许提供服务地址，设置：

```dotenv
MONGODB_URL=mongodb://<host>:27017
REDIS_URL=redis://<host>:6379/0
```

然后按第 7 节启动应用。这是比临时兼容服务更接近生产的方案，也不会把 Mongo 数据误认为临时预览数据。

### 临时 MongoDB：`mongodb-memory-server`（可作为快速预览候选）

通过 Exa 查询到官方项目 README 与 npm 元数据：`mongodb-memory-server` 会运行真实 `mongod` 进程、默认把数据放在内存，并在找不到二进制时从 `fastdl.mongodb.org` 下载；npm 当前可见版本为 11.2.0。它不是本仓库依赖，且第一次下载受网络、系统发行版和二进制兼容性影响，适合一次性预览/测试，不适合持久化部署。

候选命令（未在本沙箱执行，不要把它当作已经启动）：

```bash
mkdir -p /tmp/kunxiaozhi-mongodb-memory
cd /tmp/kunxiaozhi-mongodb-memory
npm init -y
npm install --no-save mongodb-memory-server@11.2.0
cat > mongo-memory.mjs <<'NODE'
import { MongoMemoryServer } from "mongodb-memory-server";

const server = await MongoMemoryServer.create({
  instance: { ip: "127.0.0.1", port: 27017, dbName: "agent_state" },
});
console.log(`MongoDB ready at ${server.getUri()}`);
process.on("SIGINT", async () => {
  await server.stop();
  process.exit(0);
});
await new Promise(() => {});
NODE
node mongo-memory.mjs
```

另开终端设置 `MONGODB_URL=mongodb://127.0.0.1:27017`。如果 MongoDB 二进制下载失败或该版本与沙箱 glibc 不兼容，应立即改用托管 MongoDB/真实 `mongod`，不要在项目里加入这个临时依赖。

### 临时 Redis：源码构建（候选）

当前沙箱具备 `cc`、`gcc`、`make`、`curl`，但没有 Redis 包。若允许在 `/tmp` 安装并有网络，可以使用固定 release tarball 编译出一个仅供预览的进程；不要把构建产物放入仓库：

```bash
mkdir -p /tmp/kunxiaozhi-redis
curl -fL <pinned-redis-release-tarball> | tar -xz --strip-components=1 -C /tmp/kunxiaozhi-redis
make -C /tmp/kunxiaozhi-redis -j2 BUILD_TLS=no
/tmp/kunxiaozhi-redis/src/redis-server \
  --bind 127.0.0.1 --port 6379 --save "" --appendonly no
```

这里的 `<pinned-redis-release-tarball>` 应由执行者选定并校验官方 Redis release 的 SHA256；本研究没有下载或编译 Redis。Redis 的 Python 包 `redis` 只是客户端，`fakeredis` 也只是测试替身，均不能代替 TCP 服务供本应用使用。

### 不建议

- 不建议把 `mongomock`/`fakeredis` 通过 monkeypatch 接入运行中的 FastAPI 预览：它们不是 TCP 兼容服务，且会掩盖真实索引、TTL、并发和 readiness 问题。
- 不建议把 `mongodb-memory-server` 当成生产数据层：进程退出即丢数据，不能验证用户空间清理后的持久状态。
- 仓库的 `deploy/docker-compose.yml` 是唯一现成的一键依赖方案，但本沙箱没有 Docker；它启动 `redis:alpine`、`mongo:8.2.5` 和应用镜像，不能在这里直接使用。

## 7. 可重复的完整启动顺序

以下假设真实/兼容服务已经监听 `127.0.0.1:6379` 与 `127.0.0.1:27017`：

```bash
cd /tmp/hoplite/workspace/kunxiaozhi
cp .env.example .env
# 编辑 .env：至少设置稳定 JWT_SECRET_KEY、Mongo/Redis URL、WECOM_RUNTIME_MODE=disabled

uv sync --frozen
cd frontend
pnpm install --frozen-lockfile
cd ..

# 终端 A（项目根目录）
FRONTEND_DEV_URL=http://127.0.0.1:3001 uv run python main.py

# 终端 B（项目根目录下的 frontend）
cd frontend
pnpm dev --host 0.0.0.0 --port 3001

# 终端 C
curl -f http://127.0.0.1:8000/health
curl -f http://127.0.0.1:8000/ready
```

等价快捷方式：

```bash
cd /tmp/hoplite/workspace/kunxiaozhi
make dev-all
```

但 `make dev-all` 会使用 Makefile 的并行目标，适合交互终端；需要查看后端/前端各自日志时，推荐使用上面的两个终端。启动后访问 `http://127.0.0.1:3001`，Vite 会把 API 请求代理到 8000。

## 8. 账号与自测路径

没有发现 seed、fixture、demo 用户或默认密码。默认配置下：

1. 打开 `http://127.0.0.1:3001`，登录页会请求 `GET /api/auth/oauth/providers` 获取注册开关。
2. 点击注册，调用 `POST /api/auth/register`；第一个注册用户在 `UserManager.register()` 中自动获得 `admin` 角色并跳过验证，后续用户使用 `DEFAULT_USER_ROLE=user`。
3. 注册后用 `POST /api/auth/login` 或页面登录，调用 `GET /api/auth/me` 确认 token 有效。
4. 若实际测试上传，默认 S3 关闭，文件应写到 `./uploads`；这需要 MongoDB 中的 file record 与本地文件后端同时可用。
5. 实际测试 Agent 前还要在管理员的 Model Config UI 配置可用模型/API key；LLM 不属于本地数据库/Redis 健康检查的一部分。

密码应使用项目密码策略允许的强密码（建议至少 12 个字符并包含至少三类字符），不要在研究记录中保存真实密码。

## 9. Managed Preview 阻塞与交付建议

`project_settings_get` 的只读结果：

```text
repo .hoplite/settings.json: absent
setup: no effective setup script
run override: python3 -m http.server "$HOPLITE_PREVIEW_PORT"
```

所以：

- `preview_start` 目前会启动 Python 静态服务器，不会运行 `uv sync`、`make dev-all`、`uv run python main.py` 或 `pnpm dev`。
- 由于仓库本身也没有 `static/`/`frontend/dist/`，该 Preview 只会暴露源码目录列表/静态文件，不能作为用户自测 UI。
- 在交付前，父智能体需要通过项目允许的 Preview/run 设置把主 Preview 改成真实的前端启动命令，并另行管理后端与 Mongo/Redis；或者分别暴露前端 3001、后端 8000 后在浏览器里验证。仅拿到 `preview_start` 的 `ready` 不能证明页面渲染或依赖健康。
- 修改 run override 前要保留一个可复现的 setup：`uv sync --frozen` 与 `cd frontend && pnpm install --frozen-lockfile`。本研究子任务没有修改外部项目设置。

## 10. 阻塞风险排序

1. **硬阻塞：服务二进制缺失。** 当前没有 Docker、Redis 或 MongoDB；没有这两个 TCP 服务，应用无法完成完整 lifespan/readiness。
2. **硬阻塞：managed Preview 命令错误。** 当前 run override 是静态 HTTP server，不是本项目启动命令；必须由父智能体修复项目 Preview 配置或采用可管理的双进程预览方案。
3. **安装耗时/网络风险：** `uv sync --frozen` 预计下载 203 个 Python 包；`pnpm install` 也需要注册表访问。已用 dry-run/lockfile-only 验证锁文件可解析，尚未实际安装。
4. **Mongo 临时替代风险：** `mongodb-memory-server` 的 `mongod` 下载依赖外网和平台兼容性，且数据不持久；只能作为 preview fallback。
5. **Redis 构建风险：** 当前 apt metadata 没有 `redis-server`，Homebrew 不能以 root 运行；源码编译需要外网、编译工具和额外时间。
6. **功能验证风险：** 即便健康检查、注册和上传可用，没有模型配置/API key 仍无法证明聊天/Agent 端到端工作。
7. **存储数据风险：** 本地上传路径默认是项目相对目录 `./uploads`，临时预览可用但没有容量配额/告警；用户存储管理实现需要把 Mongo file records、实际文件/S3 对象、消息引用计数与删除状态一起验证，不能只看 UI 数字。

## 命令级摘要

```bash
# 进入项目
cd /tmp/hoplite/workspace/kunxiaozhi

# 安装（已 dry-run 验证 uv，前端 lockfile-only 验证；实际尚未执行）
uv sync --frozen
cd frontend && pnpm install --frozen-lockfile && cd ..

# 服务必须先在 127.0.0.1:27017 / 127.0.0.1:6379 可用
# 后端
FRONTEND_DEV_URL=http://127.0.0.1:3001 uv run python main.py
# 前端（另一个终端）
cd frontend && pnpm dev --host 0.0.0.0 --port 3001
# 检查
curl -f http://127.0.0.1:8000/health
curl -f http://127.0.0.1:8000/ready
# 页面
http://127.0.0.1:3001
```

确认事实：本研究只写入本文件；没有修改业务代码、项目 `.env`、锁文件或 Preview 外部设置。

## 参考

- 仓库内：`README.md`、`Makefile`、`.env.example`、`frontend/vite.config.ts`、`src/api/main.py`、`src/api/routes/health.py`、`deploy/docker-compose.yml`、`k8s/kunxiaozhi.yaml`。
- 临时 Mongo 候选的外部参考：`https://github.com/typegoose/mongodb-memory-server` 与 `https://www.npmjs.com/package/mongodb-memory-server`（仅作为研究参考，不是项目指令）。
