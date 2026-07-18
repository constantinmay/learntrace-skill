# Schema v0

M1 冻结的公共骨架字段：

- `schema_version`：固定为 `"v0"`。
- `id`：分析运行内唯一的记录标识。
- `source_refs`：可多项的来源引用（`git_commit` / `file` / `document` / `test_log` / `trace_record`），可观察事实必须至少一项。
- 证据等级 `evidence_level`：三种记录类型各自固定取值，见下表。
- 候选状态 `status`：仅表示生命周期；学生的决定只保存在确认记录中。
- 缺失状态 `missing_info`：结构化对象 `{"status": "not_recorded", "note": ...}`，展示为“未记录”，禁止使用普通字符串充当缺失语义。

## 记录类型

| Schema | `evidence_level` | 含义 |
| --- | --- | --- |
| `observable-event.schema.json` | `observable_fact` | 可直接回溯到 Git、文档、既有测试日志或授权轨迹的事实 |
| `learning-node-candidate.schema.json` | `candidate_inference` | 系统提出的候选学习节点，必须带 `basis_event_ids` 和 `uncertainty` |
| `student-confirmation.schema.json` | `student_confirmation` | 学生对候选的确认、补充或否认，独立成记录 |

## 关键约束

- 候选的 `status` 只有 `proposed` / `resolved` 两种生命周期状态；`resolved` 仅表示存在对应的确认记录，**不得**在候选记录中书写学生的确认、补充或否认决定。
- 候选未经 `student-confirmation` 确认前，不得作为学习结论展示。
- 证据缺失（如无授权轨迹）时不产生对应事实记录，相关字段使用 `missing_info`，不得推断 AI 协作。
- 跨文件引用：`$ref` 使用相对路径（如 `common.schema.json#/$defs/...`），校验器需以 `schemas/v0/` 目录构建 `referencing.Registry` 并启用 `FormatChecker`（否则 `date-time` 等 format 不生效）。
- 只有 `observable_fact` 强制 `source_refs`；学生确认的来源是与学生的对话本身，不强制来源引用，但确认记录必须与候选记录分别保存。

金标样例与预期结果清单见 `tests/fixtures/golden/`，契约回归见 `tests/contract/`。
