# 用户存储管理最终规划审查（Revision 3）

## 结论

建议进入用户审批。Revision 3 没有残余阻塞级 P0/P1，任务仍保持 `planning`，产品代码尚未修改。

## 已确认关闭

- 旧跨用户对象使用独立 blob/owner、归属重建、轮换与保守 quarantine；未知对象不签名、不删除。
- raw-key 路径使用边界安全校验，system-artifact 兼容读取采用固定 allowlist；managed URL 先查 lifecycle/tombstone。
- standalone Mongo 使用持久 intent、normalized items、CAS lease/version、配额 marker、generation 和 forward/compensating recovery。
- 受保护资源增长在指针切换前计费；缩容只在新 owner 与领域指针生效后释放，崩溃最多临时多计费。
- operation items 和所有自由文本均有可执行边界，避免 MongoDB 16 MiB 文档膨胀；Skill/WeCom 批量和流式入口有总量预留与补偿。
- Profile/Persona/Team avatar 与 Skill 为受保护资源；聊天/WeCom 可由空间管理直接删除。
- 历史附件保留 tombstone；消息引用不阻止用户明确删除；Agent 的 direct/queued/ARQ/vision/document 路径使用服务端权威状态。
- 策略 A 范围一致：生成物、Reveal、tool binary/project 不进入个人配额、列表或通用删除。

## 实现期强制门槛

- 覆盖 stale preparing writer fencing、同 source-ref 并发替换、同用户去重索引竞争及每个跨系统边界的故障注入。
- 真实 MongoDB、Redis、FastAPI、Vite 和浏览器路径必须分别健康后才完成 AC9；静态 Preview 或 fake service 不得替代。
- 已签发 legacy S3 URL 只能等待原 TTL 到期，此限制必须如实报告。

本轮审查只评估规划，没有实现产品代码。
