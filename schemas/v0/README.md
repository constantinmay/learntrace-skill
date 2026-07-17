# Schema v0

M1 首先冻结以下公共字段：

- `schema_version`
- 记录 `id`
- 可包含多项的 `source_ref`
- 证据等级
- 候选状态
- 缺失状态

正式 JSON Schema 和金标样例由数据契约任务线通过独立 PR 提交。
