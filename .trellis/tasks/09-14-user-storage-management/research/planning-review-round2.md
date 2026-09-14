# 用户存储管理规划终审（Revision 2）

## 结论

Revision 2 已关闭原审查中的三个 P0，但仍有两个阻止审批的 P1，因此暂不进入实现。

## 残余 P1

1. `design.md §6 Replace` 在受保护资源换成更小文件时先提交负 `charge_delta`、后切换领域指针。两步之间崩溃会在旧文件仍生效时少计费，并可能让并发上传越过硬上限。修订要求：增长差额在切换前计费；缩容差额仅在新指针和 owner 生效后幂等释放，任何崩溃最多临时多计费。
2. `user_files` 声明了 `(user_id,source,content_hash)` 索引却缺少 `content_hash` 字段；批量操作数组和 blob pending-owner 列表也没有可执行上限，存在 MongoDB 16 MiB 文档风险。修订要求：补齐字段并使用有明确条数、字段长度、manifest 大小限制的 normalized operation items。

## 非阻塞收口项

- 为 legacy raw-read 明确系统产物 prefix allowlist、缓存策略和签名 TTL 测试。
- 将不存在的泛化测试路径替换为明确将创建的 focused test files，并列出缩容崩溃、路径穿越、未知 key、WeCom/Skill 边界等场景。
- 标注初始后端研究中 generated/Reveal/message-ref 建议已被最终策略 A 和删除语义取代。
- 真实 MongoDB、Redis、FastAPI、Vite Preview 仍须在实现阶段验证；无法取得真实服务时 AC9 必须保持未完成。

本轮审查只评估规划，没有修改产品代码或任务状态。
