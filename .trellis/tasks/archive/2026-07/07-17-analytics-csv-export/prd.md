# 统计 CSV 导出

## Goal

用户钻取列表与会话钻取列表支持一键导出 **当前筛选结果** CSV。

## Requirements

- Export API 与 list 使用同一套 filter/sort 参数。
- 前端「导出」下载文件；UTF-8（含 BOM 若需 Excel 中文）。
- MVP 仅 CSV，不做 xlsx。

## Acceptance Criteria

- [ ] 会话列表在某筛选下导出的行集与列表一致（export 为当前筛选全量，UI 可标明）。
- [ ] 用户列表同样可导出。
- [ ] 无筛选权限外数据泄露（沿用 analytics 鉴权）。

## Dependencies

- **串行依赖** `07-17-analytics-dual-dimension-drilldown` 的 list filter 契约。

## Notes

- 父任务：`07-17-persona-template-and-analytics`
