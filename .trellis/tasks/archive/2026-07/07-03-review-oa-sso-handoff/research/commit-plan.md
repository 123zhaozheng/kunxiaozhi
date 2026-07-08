# OA SSO 功能提交计划

## Commit 1: OA SSO 企业免密登录功能

**建议提交信息：**
```
feat(auth): add enterprise OA SSO passwordless login

- 参考 fastapi_web 实现对接行内 OA 门户免密登录
- 后端：SM2 加密 + 2 次 token 刷新；Mock 模式便于本地开发
- 自动注册：工号作为 username，邮箱 {工号}@ksrcb.com
- AUTO_PROVISION 开关：false 时未开通用户返回 403 联系管理员
- 前端：/auth/oa 深链静默登录页 + 轨道环 + 四步进度清单
- 配置面板 + 环境变量 + i18n（zh/en）
- 限流 10/min/IP + 认证白名单
```

**包含文件：**

### 新增文件
- `docs/oa-sso-handoff.md`
- `src/infra/crypto/__init__.py`          （修复：必须加）
- `src/infra/crypto/sm2_utils.py`
- `src/infra/auth/oa_sso.py`
- `src/infra/auth/oa_sso_mock.py`
- `src/infra/auth/oa_login.py`
- `src/api/routes/auth/oa_sso.py`
- `tests/infra/auth/test_oa_login.py`
- `tests/infra/auth/test_oa_sso_mock.py`
- `frontend/src/components/auth/OaSsoHelpDialog.tsx`
- `frontend/src/components/auth/OaSsoLogin.tsx`
- `frontend/src/components/auth/OaSsoProgress.tsx`

### 修改文件
- `pyproject.toml`                        （+ gmssl>=3.2.2）
- `.env.example`                          （OA_SSO_* 配置段）
- `src/kernel/config/base.py`
- `src/kernel/config/_definitions_extra.py`
- `src/api/middleware/auth.py`            （白名单 + /api/auth/login/oa-sso）
- `src/api/routes/auth/__init__.py`
- `src/api/routes/auth/oauth.py`          （providers 暴露 oa_sso）
- `frontend/src/App.tsx`                  （路由 /auth/oa）
- `frontend/src/components/auth/AuthPage.tsx`
- `frontend/src/services/api/auth.ts`
- `frontend/src/i18n/locales/zh.json`
- `frontend/src/i18n/locales/en.json`
- `frontend/src/styles/auth.css`
- `uv.lock`                               （gmssl 导致的锁定变更）

**不包含**：任何 .agents/ .claude/ .pi/ .trellis/ 平台变更

---

## Commit 2: Trellis 平台升级 + AI 工具配置入库（单独提交）

**建议提交信息：**
```
chore(platform): track trellis/agent workspace and local AI tool configs

- 跟踪 .claude/ .pi/ .code/ .cursor/ .codex/ 配置
- .agents/ 新增技能（trellis-channel / meta / spec-bootstrap / session-insight）
- .trellis/ 脚本与 workflow 更新（本次平台升级）
- .gitignore 调整（允许上述 AI 工具目录被跟踪）
- 无应用功能变更
```

**包含文件（示例，实际以 git status 为准）：**

- `.gitignore`（我们之前加的否定规则）
- `.agents/**/*`
- `.claude/**/*`
- `.pi/**/*`
- `.codex/**/*`（如果有内容）
- `.trellis/` 下脚本、config、workflow 等更新
- `AGENTS.md` 等 meta 文档更新
- 其他纯平台变更

**注意**：
- uv.lock 里如果有纯平台依赖变更，要小心拆分；本次主要是 gmssl，属于 OA 功能，应放 Commit 1。

---

## 执行顺序建议

1. 先修复缺陷：
   ```bash
   touch src/infra/crypto/__init__.py
   ```

2. 提交 OA 功能（Commit 1）：
   ```bash
   git add -A -- src/infra/crypto/ src/infra/auth/oa_* src/api/routes/auth/oa_sso.py tests/infra/auth/test_oa_* docs/oa-sso-handoff.md pyproject.toml .env.example src/kernel/config/ src/api/middleware/auth.py src/api/routes/auth/__init__.py src/api/routes/auth/oauth.py frontend/src/App.tsx frontend/src/components/auth/{AuthPage.tsx,OaSso*.tsx} frontend/src/services/api/auth.ts frontend/src/i18n/locales/*.json frontend/src/styles/auth.css uv.lock
   git status   # 仔细核对
   git commit -m "feat(auth): add enterprise OA SSO passwordless login"
   ```

3. 再提交平台变更（Commit 2）：
   ```bash
   git add .agents .claude .pi .codex .trellis .gitignore AGENTS.md ...
   git commit -m "chore(platform): track trellis/agent workspace and local AI tool configs"
   ```

4. （可选）推送或开 PR。

---

## 审查结论速查

- Handoff 文档：写得对、合理
- 实现与文档一致性：高
- 阻塞问题：无（仅一个简单 hygiene 问题：缺 __init__.py）
- 推荐操作：修一下 __init__.py 后，按上面两个 commit 拆分提交
