# AI 轨迹分段说明（Issue #28）

这项功能只做一件事：把已经授权、已经脱敏的零散工具事件，确定性地分成若干连续
工作段。它不写报告、不猜学习结论，也不把 AI 活动和 Git 提交配对。

## 数据边界

Task 3 现在有三个不同层次的数据：

1. `events`：原子 `trace_record`，仍是可追溯事实；
2. `TraceEventMetadata`：适配器解析工具调用时直接得到的安全结构化字段，随显式
   Task 3 结果保存，用于重新校验分段，但不进入最终学习档案；
3. `work_segments`：引用原子事件 ID 的确定性分组，写入 Task 3 结果。

分段代码不会从 `ObservableEvent.summary` 反向猜工具、路径或命令。`summary` 是给人读的
文字，不是结构化数据源。OpenCode、Claude Code 和 Codex 适配器在仍持有原始工具字段时，
直接生成安全元数据；绝对路径、home 路径和 `..` 路径在这里就被丢弃。

宿主 Agent 的人话摘要继续使用现有 `narrative-payload.schema.json` 中的
`ai_collaboration.episodes`，由 Task 4 校验和渲染。本任务不再维护另一套
`segment_summaries` 生命周期。

## 分段规则

规则固定、可复现，不调用模型，也不执行轨迹中的命令：

1. 先按有效 `occurred_at` 排序；没有有效时间的记录放在后面；
2. 相邻记录间隔严格大于 30 分钟时切段，恰好 30 分钟不切；
3. 当前段与下一条记录的项目对象范围完全不相交时切段，例如
   `src/frontend/...` 切换到 `src/backend/...`；
4. 没有安全结构化路径时，不凭文字摘要猜对象范围。

段 ID 由排序后的原始事件 ID 计算。同一批输入换顺序，结果不变。`boundary_before`
只可能是 `time_gap` 或 `object_switch`。

## 正式输出契约

`work_segments` 中每项都通过 v0 的 `trace-work-segment.schema.json` 校验：

```json
{
  "schema_version": "v0",
  "id": "segment-0123456789abcdef",
  "event_ids": ["evt-trace-a", "evt-trace-b"],
  "time_range": {
    "start": "2026-08-30T01:00:00Z",
    "end": "2026-08-30T01:12:00Z"
  },
  "tools": ["read", "bash"],
  "paths": ["src/app.py"],
  "command_categories": ["查文件", "跑测试"],
  "source_hosts": ["opencode"],
  "session_ids": ["session-1"]
}
```

命令类别只有五种：`装依赖`、`跑测试`、`构建部署`、`查文件`、`其他`。分段只保存
类别，不保存命令参数。`TraceWorkSegment` 在写盘前会根据事件和内存元数据重新计算并
逐字段比较；遗漏事件、重复引用、倒序、伪造时间/路径/工具等都会被拒绝。

完整 Task 3 文件还通过 `trace-result.schema.json` 校验，并带有
`artifact_type=learntrace_task3_result`。`not_authorized`、`not_provided` 和
`authorized_not_found` 状态不能夹带事件或分段。

## Python 调用

调用方已经有结构化字段时，可以这样使用：

```python
from learntrace.adapters import build_trace_event_metadata, segment_trace_events

metadata = (
    build_trace_event_metadata(
        event.id,
        tool="bash",
        command="uv run pytest tests/unit -q",
    ),
)
segments = segment_trace_events((event,), metadata=metadata)
```

直接只传 `events` 仍可按时间分段，但不会从 `summary` 猜工具、路径或命令类别。

## 授权输入边界

普通项目目录扫描不再接收裸 `trace_record`，也不会因为目录中恰好有一个结构合法的
JSON 就把它写进报告。统一 `run` 流程把本轮已经完成逐来源授权的
`TraceAdapterResult` 作为显式对象交给归档层；落盘的 Task 3 文件不会被普通扫描再次
隐式导入。分阶段运行时必须对 `archive` 明确传入
`--trace-result .learntrace/task3-result.json`；读取器会校验 schema，并用同文件中的
安全结构化元数据重新计算工作段；分段与事件/元数据不一致时会被拒绝。

这里检查的是文件形状和内部确定性一致性，不是数字签名。显式指定 Task 3 文件表示调用方
信任该文件来自刚才完成授权的适配器流程；结构化元数据是重算分段的输入。不要手工编辑该
文件，也不要把来源不可信的文件当成授权结果。若要证明文件没有被第三方整体改写，需要在
文件之外另设可信签名或重新读取已授权原始导出，不属于本次分段契约。

这条边界不是“JSON 上有个字段就等于授权”。授权发生在适配器读取原会话文件之前；
Task 3 结果只记录该授权流程已经产生的最小化事实。没有授权时，不读取原会话、不产生
`trace_record` 或分段，也不能据此推断学生没有使用 AI。

## 测试与证据限制

合成测试覆盖时间边界、对象切换、五类命令、稳定 ID、重复导出去重、绝对路径隔离、
分段篡改拒绝、schema 和未授权输入边界。公开仓库没有 `107-contest` 与 `PY-ECO` 的
授权原始会话，因此不能声称已经重跑这两个真实样例。
