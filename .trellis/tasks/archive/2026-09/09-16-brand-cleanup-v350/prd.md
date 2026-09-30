# 登录页移除 Clivia 署名、版本号升至 3.5.0、清除 OA 帮助文案 LambChat 残留

## 背景

产品已从 LambChat 重塑为「昆小智」（见归档任务 06-18-lambchat），但仍残留三处品牌问题：

1. 登录页 footer 显示「由 Clivia 驱动 昆小智」（Clivia 为旧署名，需删除）。
2. 「如何从 OA 登录」帮助步骤中出现旧名 LambChat。
3. 关于面板显示的应用版本仍为 2.5.0，需升至 3.5.0。

## Requirements

### 1. 删除登录页「由 Clivia 驱动」署名

- `frontend/src/components/auth/AuthPage.tsx:709-711`：footer 中 `<span>{t("auth.poweredBy")} {APP_NAME}</span>` 整行删除；该 span 删除后只剩 `·` 分隔符和年份，分隔符 span（line 712）一并删除，footer 保留年份。
- 删除 5 个语言包中的 `auth.poweredBy` key：
  - `frontend/src/i18n/locales/zh.json:935`（"由 Clivia 驱动"）
  - `frontend/src/i18n/locales/en.json:935`（"Powered by Clivia"）
  - `frontend/src/i18n/locales/ru.json:903`、`ko.json:901`、`ja.json:901`
- **不动**：`common.poweredBy`（zh.json:519 等，ProfileModal 使用，不含 Clivia）；`CITATION.cff` / `LICENSE` 中的 Clivia（用户未要求）；测试 fixture `clivia.yang`。
- 若删除后 `APP_NAME` 在 AuthPage 中无其他引用，同步移除该 import。

### 2. 应用版本 2.5.0 → 3.5.0

- `pyproject.toml:3`：`version = "2.5.0"` → `version = "3.5.0"`。
- 该值经 `src/kernel/config/utils.py get_app_version()` → `APP_VERSION` → `/api/version` → 前端「关于 昆小智」面板展示。
- **不动**：`frontend/package.json`、`src-tauri/tauri.conf.json`、`src-tauri/Cargo.toml` 的 2.4.1（桌面端独立版本号，与用户所指「昆小智 v2.5.0」无关）。

### 3. 清除 OA 登录帮助中的 LambChat 残留

- `frontend/src/i18n/locales/zh.json:495`：`"howToStep2": "在应用菜单中点击 LambChat"` → `"在应用菜单中点击 昆小智"`。
- `frontend/src/i18n/locales/en.json:495`：`"howToStep2": "Open LambChat from the app menu"` → `"Open 昆小智 from the app menu"`（en 语言包其余处均直接用「昆小智」字面量，保持一致）。
- ru/ko/ja 语言包无 LambChat 残留，无需改动。
- 附带发现（同类残留，一并修复）：`frontend/src/components/panels/WeComNetworkSettings.tsx:265` placeholder `/etc/lambchat/certs/bank-ca.pem` → `/etc/kunxiaozhi/certs/bank-ca.pem`。

### 4. 默认头像配色：淡绿渐变、中心偏白（2026-09-16 追加）

将默认头像（无自定义头像时）的琥珀→橙渐变改为「边缘淡绿 → 中心白」的径向渐变，共 5 处：

- `frontend/src/components/profile/tabs/ProfileInfoTab.tsx:173`（大头像）
- `frontend/src/components/layout/UserMenu.tsx:280`
- `frontend/src/components/panels/SidebarParts/SidebarRail.tsx:212`
- `frontend/src/components/panels/SidebarParts/SessionListContent.tsx:502`
- `frontend/src/components/layout/AppContent/MessageOutlinePanel.tsx:61`

改法（Tailwind 3.4，用任意值径向渐变）：
- `bg-gradient-to-br from-amber-400 to-orange-500` → `bg-[radial-gradient(circle,#ffffff_25%,#bbf7d0_100%)]`（green-200 边缘、白色中心）
- 5 处首字母 `text-white` → `text-emerald-600`（白底上保证可读）

## Acceptance Criteria

- [ ] 登录页 footer 不再出现「Clivia」，布局正常（仅年份或年份分隔符清理后的形态）。
- [ ] `grep -i clivia frontend/src` 仅剩测试 fixture `clivia.yang`（或为 0）。
- [ ] 「如何从 OA 登录」帮助步骤 2 显示「昆小智」，无 LambChat。
- [ ] `grep -i lambchat frontend/src` 零命中（src-tauri 与后端内部命名不在本次范围）。
- [ ] `/api/version` 返回 `3.5.0`，「关于 昆小智」面板显示新版本。
- [ ] 删除 `auth.poweredBy` 后无悬空 i18n 引用（含 5 个语言包与组件）。

## Notes

- 轻量任务，PRD-only。
- 仅改文案/版本号，不改功能逻辑。
