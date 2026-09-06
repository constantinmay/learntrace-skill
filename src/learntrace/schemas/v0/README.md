# Schema v0

M1 冻结的公共骨架字段：

- `schema_version`：固定为 `"v0"`。
- `id`：分析运行内唯一的记录标识。
- `source_refs`：可多项的来源引用（`git_commit` / `file` / `document` / `test_log` / `trace_record`），可观察事实必须至少一项。
- 证据等级 `evidence_level`：三种记录类型各自固定取值，见下表。
- 候选状态 `status`：仅表示生命周期；学生的决定只保存在确认记录中。
- 候选分类 `node_type`：必填枚举，取值为 `follow_up` / `revise_ai_suggestion` / `fix_failed_approach` / `add_tests` / `adjust_constraints`，档案层按此筛选与统计；新增类型属于契约变更。
- 候选 `uncertainty` 必须以「高：」「中：」或「低：」程度前缀开头，Schema `pattern` 强制。
- 缺失状态 `missing_info`：结构化对象 `{"status": "not_recorded", "note": ...}`，展示为“未记录”，禁止使用普通字符串充当缺失语义。

## 记录类型

| Schema | `evidence_level` | 含义 |
| --- | --- | --- |
| `observable-event.schema.json` | `observable_fact` | 可直接回溯到 Git、文档、既有测试日志或授权轨迹的事实 |
| `learning-node-candidate.schema.json` | `candidate_inference` | 系统提出的候选学习节点，必须带 `basis_event_ids` 和 `uncertainty` |
| `student-confirmation.schema.json` | `student_confirmation` | 学生对候选的确认、补充或否认，独立成记录 |

Task 3 另有两个批次/索引契约，它们不是新的证据等级：

| Schema | 含义 |
| --- | --- |
| `trace-event-metadata.schema.json` | 适配器从已授权工具记录直接提取的安全结构化字段；用于重算分段，不读取自然语言摘要 |
| `trace-work-segment.schema.json` | 由已授权 `trace_record` 确定性计算出的工作段索引；它引用原子事件，不新增事实 |
| `trace-result.schema.json` | 一次 Task 3 适配结果的授权边界；Schema 约束字段形状和授权状态，serializer/loader 的确定性重算约束跨对象一致性 |

## 关键约束

- 候选的 `status` 只有 `proposed` / `resolved` 两种生命周期状态；`resolved` 仅表示存在对应的确认记录，**不得**在候选记录中书写学生的确认、补充或否认决定。
- 候选未经 `student-confirmation` 确认前，不得作为学习结论展示。
- 证据缺失（如无授权轨迹）时不产生对应事实记录，相关字段使用 `missing_info`，不得推断 AI 协作。
- 跨文件引用：`$ref` 使用相对路径（如 `common.schema.json#/$defs/...`），校验器需以 `schemas/v0/` 目录构建 `referencing.Registry` 并启用 `FormatChecker`（否则 `date-time` 等 format 不生效）。
- 只有 `observable_fact` 强制 `source_refs`；学生确认的来源是与学生的对话本身，不强制来源引用，但确认记录必须与候选记录分别保存。
- 普通目录扫描不得把裸 `trace_record` 当成已授权输入；轨迹只能由本轮显式授权并通过 `trace-result.schema.json` 与确定性重算校验的 Task 3 结果进入归档。
- Task 3 结果读取器保证 schema 合规和事件、结构化元数据、分段之间的内部一致性；它不是数字签名，显式选择来源不可信或被整体改写的文件不在该保证内。

