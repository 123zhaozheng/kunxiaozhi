# 上传接口危险后缀拦截

## Goal

在不扩大上线改造范围的前提下，只加固现有主文件上传接口 `POST /api/upload/file`：服务端在读取和持久化文件前拒绝明确危险的文件后缀，避免 `UNKNOWN` 分类绕过现有分类白名单。

## Confirmed Facts

- 主上传接口位于 `src/api/routes/upload.py:393-555`。
- 当前逻辑仅在文件被识别为已知 category 时检查该 category 的允许后缀；`FileCategory.UNKNOWN` 不检查后缀，因此任意未知/危险扩展名可能进入存储。
- 头像接口已有 `jpg/jpeg/png/gif/webp` 独立允许列表，本次不修改。

## Requirements

- 只修改主文件上传接口及其聚焦测试，不改其他上传、导入、存储或下载链路。
- 在读取请求体、写临时 spool、查重或写对象存储前，从规范化文件名提取最终后缀并执行服务端拒绝列表。
- 后缀比较大小写不敏感；`photo.jpg.EXE`、`archive.tar.exe` 等复合文件名按最终后缀拒绝。
- 拒绝列表至少覆盖：操作系统可执行/安装/驱动、命令与脚本、服务端 Web 脚本、浏览器主动内容以及宏启用 Office 文件。
- 被拒绝时返回 HTTP 400 和稳定、可理解但不泄露内部路径的错误信息；不得创建临时持久化对象或文件记录。
- 不改变现有合法类型、大小限制、权限、去重、响应和前端行为。

## Acceptance Criteria

- [ ] `.exe`、`.dll`、`.bat`、`.cmd`、`.ps1`、`.sh`、`.php`、`.jsp`、`.asp/.aspx`、`.html/.htm`、`.svg`、`.jar`、`.msi`、`.docm/.xlsm/.pptm` 等危险后缀被服务端拒绝。
- [ ] 大小写和复合文件名变体同样被拒绝。
- [ ] 现有合法文件和 `UNKNOWN` 分类中的非拒绝列表后缀保持原有行为。
- [ ] 聚焦测试证明拒绝发生在读取上传内容和调用存储前。

## Out Of Scope

- 内容签名/MIME 检测、图片重编码、PDF/Office 解析、异步扫描或杀毒。
- 头像、Skill ZIP、Skill binary、Agent base64、URL/sandbox、`read_document` 等其他入口。
- 文件读取/签名/删除授权、公开代理、安全响应头、历史文件或存储元数据迁移。
- 前端允许类型同步和现有文件类型兼容性收紧。
