# 合入 kunxiaozhi 09-18 存储URL与read_document 交接包

## Goal

将 `D:\code\python\temp\kunxiaozhi-fix\kunxiaozhi-09-18-storage-url-and-read-document.patch`
（4 个提交：存储 URL 收敛 + 删除后重传修复 + MinerU 图片分析 + read_document 路径分发）
合入 feat/user-storage-management，与本地 3 个散装存储 URL 修复（f71efc09 / 93948609 / 7bc040cb）正确汇合。

技术设计、根因与验证证据以交接包自带产物为准：
`D:\code\python\temp\kunxiaozhi-fix\trellis-task\{prd,design,implement,HANDOFF}.md`，
patch 应用后落在 `.trellis/tasks/09-18-storage-url-and-read-document/`。

## Requirements

- 补丁 base `0824d046` = 当前 origin/main，fetch 后已在本地，可用 `git am -3` 三方合并
- `.trellis/.developer` 冲突保留本地版本（对方身份文件不合入）
- `src/infra/agent/attachments.py` / `src/infra/tool/read_document_tool.py` 与本地修复冲突，
  以补丁终态（content_url helper 收编版）为准，但需逐项核对本地修复无独有逻辑丢失
- 前端 `AttachmentPreviewHost.tsx` 本地改动（7bc040cb / 93948609）不在补丁内，须保留并确认仍兼容新 URL 契约
- 新增部署配置项（MINERU_BACKEND / MINERU_PARSE_EFFORT=high / MINERU_IMAGE_ANALYSIS /
  MINERU_RETURN_IMAGES / APP_BASE_URL）需提示部署侧（k8s yaml）

## Acceptance Criteria

- [x] `git am -3` 完成 4 个提交合入，无未解决冲突残留
- [x] 本地 93948609 加的 `tests/infra/tool/test_read_document_tool.py` 87 行测试（不在补丁内）全部通过
- [x] 定向测试通过：`uv run pytest tests/infra/storage/ tests/infra/tool/ tests/api/test_storage_content_public_path.py -q`
- [x] 本地已有测试无定向范围回归（f71efc09 的 test_attachment_lifecycle / test_auth_middleware_whitelist）
- [x] `.trellis/.developer` 未被对方覆盖

## Notes

- 交接包声明全量 2138 passed / 11 failed（11 项与未打补丁 main 一致），按 verify-scope-targeted 原则本地只跑定向，全量先问再跑
- 补丁不含 uv.lock 改动；`uv run` 再生 uv.lock 的坑提交前注意排除

## 结果（2026-09-20）

- git am -3 落 4 提交 + 1 合并修正（1bdde899）：auth.py 删宽前缀收敛到精确正则、白名单测试假 id 改真实形态
- attachments.py 冲突解=补丁 build_content_url(文件名) + 本地 _absolute_storage_url(主机前缀) 叠加
- 定向 453 passed；mcp pubsub 锁续期、wecom preferred_agent 两项失败经 stash 基线对照确认预存
- .trellis/.developer 被 am -3 覆盖为 hoplite，已 amend 出库并恢复 zhaozheng（untracked）
- 交接包 4 个 MINERU_* 与 APP_BASE_URL 配置项待部署侧（k8s）补

## 追加（2026-09-20 下午）：image-unify 合入 + review 4 项修复

- `kunxiaozhi-image-unify.patch`（a99bb39f，单提交）`git am -3` 零冲突合入 → f7bb98a7：
  多模态原生 image_url（相对 URL 补请求 base_url/APP_BASE_URL 前缀），非多模态一律
  read_document(MinerU)；删除 vision_assist 支路/ENABLE_VISION_ASSIST 配置/前端设置项/
  5 locale 孤立键。验证：206 passed、tsc 0、eslint 干净
- review 4 项 🟡（探测收窄+HEAD、删 _resolve_url 孤儿、移除 no-op 的 MINERU_RETURN_IMAGES
  链路、@tool 描述同步新契约）派 trellis-implement 修复中
- 部署注意更新：k8s 不需要 MINERU_RETURN_IMAGES（已删除）、ENABLE_VISION_ASSIST 已废弃

## 最终状态（2026-09-20）

- 8d53d7fa review 4 项修复落地（trellis-implement 实现 + trellis-check 复验全过，含
  /content/report 无扩展名段边界确认与 design §7 一致），spec 补探测契约小节
- 今日累计 7 提交：1b041089/7f8d9c2f/aa4f4232/936adf1c(09-18 包 4 提交) + 1bdde899(合并修正)
  + f7bb98a7(image-unify) + 8d53d7fa(review 修正)
- 验证：定向 266 passed + agents 206 passed + tsc/eslint 全绿；预存失败 2 项与合并无关
- 部署清单（k8s）：MINERU_BACKEND=hybrid-engine、MINERU_PARSE_EFFORT=high、
  MINERU_IMAGE_ANALYSIS=true、APP_BASE_URL=<可访问地址>；MINERU_RETURN_IMAGES 已删除勿配，
  ENABLE_VISION_ASSIST 已废弃
- 真机待验：删除后重传、中文文件名下载、PDF 图表描述、直接读 png/jpg、APP_BASE_URL 未配时的降级
