# AI 轨迹分段说明（Issue #28）

这份说明解释 LearnTrace 如何把许多条零散的工具记录整理成较短的“工作过程”。
它面向需要调用适配器或编写宿主 Agent 的开发者，也可以作为联调时的约定。

## 先记住三个层次

一条工具记录只说明“某个工具在某个时间做过某件可观察的事”。它是原始事实，
类型为 `trace_record`，保存在 `events` 中。

一段工作过程只是把几条已经存在的事实放在一起，并保存这些事实的 ID。它不是
新的事实，也不是学习结论。它保存在可选的 `work_segments` 中。

一段文字摘要由宿主 Agent 写，不由 CLI 猜测。它必须单独放在
`segment_summaries` 中，并注明是自动整理、可以被学生否认。这样既能让报告易读，
也不会把 Agent 的理解冒充成轨迹原文。

```text
原始导出 → ObservableEvent（事实）
             ↓ 确定性分段
           work_segments（事实 ID 的分组）
             ↓ 经授权后由宿主 Agent 写摘要
           segment_summaries / narrative episodes（派生文字）
```

## 怎样决定一段从哪里开始

默认规则是固定的，不调用模型，也不执行命令：

1. 先按有效的 `occurred_at` 从早到晚排序；没有有效时间的记录放在后面，且不会
     被猜成某个时间。
2. 相邻两条记录的时间差严格大于 30 分钟时，新记录开启下一段。恰好 30 分钟仍
     属于同一段。
3. 如果当前段已经操作了一个明确的项目对象范围，而下一条记录只操作另一个不
     相交的范围，也开启下一段。例如 `src/frontend/...` 与 `src/backend/...` 会
     分开；`src/a.py` 与 `src/b.py` 仍可留在同一个 `src` 范围内。
4. 没有路径、只有脱敏占位符或路径不安全时，不用它制造边界。这样宁可少分段，
     也不会因为脱敏结果而编造工作过程。

每段都有稳定 ID：它由按时间排序后的原始事件 ID 计算得到。同一批事件换输入顺序
不会换 ID。`boundary_before`（如果存在）说明该段是因为 `time_gap` 还是
`object_switch` 开始的。

## `work_segments` 的字段

适配器结果和归档只新增字段，不删除原有 `events`：

```json
{
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

- `event_ids` 是必需的追溯链；每个 ID 必须能在同一份归档的 `events` 中找到，且
  对应事件必须是 `trace_record`。
- 时间、工具、路径、宿主和会话都是已经经过适配器过滤的标签。分段函数不会回读
  导出文件，也不会把原始聊天或工具输出带进来。
- `command_categories` 只使用五个固定值：`装依赖`、`跑测试`、`构建部署`、
  `查文件`、`其他`。参数、密钥和路径不属于这个字段。
- `work_segments` 为空是正常情况，例如没有授权轨迹或授权导出没有可用工具记录。
  这时仍保留原有的状态和告警，不应据此推断“没有使用 AI”。

Python 调用入口：

```python
from learntrace.adapters import segment_trace_events

segments = segment_trace_events(events)  # events 可以包含非 trace_record，函数会忽略它们
```

也提供 `aggregate_trace_events` 与 `build_work_segments` 两个同义名称，方便不同
调用方迁移；推荐新代码使用 `segment_trace_events`。

## 命令类别怎样来

适配器原来已有 `summary` 的隐私过滤规则，本任务没有改掉它。分段时只读取其中的
安全命令标签，再用 `classify_command()` 做固定分类：

| 类别 | 例子（只用于说明，不会把参数写入分段） |
| --- | --- |
| 装依赖 | `pip install`、`uv sync`、`npm install` |
| 跑测试 | `pytest`、`uv run pytest`、`npm run test` |
| 构建部署 | `npm run build`、`docker build`、`kubectl apply` |
| 查文件 | `ls`、`cat`、`git status`、`git log` |
| 其他 | 无法安全归入前四类的命令 |

分类函数只返回类别枚举，不返回原始命令。适配器继续使用原有的
`summarize_command()`；已有的 `git status`、`uv run pytest` 等安全摘要保持兼容，
对能安全识别的安装、构建脚本会额外保留一个无参数的子命令标签，便于归类。

## 宿主 Agent 摘要和授权

分段本身不产生自然语言摘要。宿主 Agent 在拿到学生对具体会话文件的授权后，才可
使用分段作为输入：

```python
agent_input = segment.to_agent_input(authorization="minimal")
```

`minimal` 输入只含时间、工具、规范化相对路径、命令类别和来源宿主这五类内容，
并保留段 ID/原始事件 ID 作为追溯用的元数据；它不含会话 ID、聊天正文、工具输出、
完整命令或绝对路径。`full` 投影才会附带会话 ID，供宿主 Agent 在另有逐文件全文授权
时定位可回读的会话。

`full` 只表示“另有逐文件全文授权，宿主 Agent 可以按引用回读原会话”；它不会把
全文偷偷塞进 `work_segments`。没有对应授权记录时，不能使用 `full`。

宿主 Agent 写出的每段摘要应使用如下形状（字段名与 narrative 的派生 episode 对齐）：

```json
{
  "id": "summary-segment-0123456789abcdef",
  "segment_id": "segment-0123456789abcdef",
  "label": "测试失败后的修正",
  "body": "宿主 Agent 根据获准内容整理的短摘要。",
  "event_ids": ["evt-trace-a", "evt-trace-b"],
  "derived": true,
  "derivation": "host_agent_episode_digest",
  "deniable": true,
  "authorization": "full",
  "status": "active"
}
```

摘要必须引用所属分段中的原始事件 ID，且只能说明授权内容直接支持的做法、目标或
卡点；不能代写学生反思，也不能把 AI 轨迹和 Git 提交说成已确定的对应关系。

## 否认、无授权和兼容性

- `status: "denied"` 的摘要不会出现在 Markdown 正文。机器归档仍保留摘要的状态、
  引用和原子 `events`，方便审计和回滚；摘要正文不会被渲染。
- 未授权导出不会产生 `trace_record`，也不会产生 `work_segments`。报告应说明是未
  提供、未授权，还是已授权但未找到，而不是写“没有 AI 协作”。
- 旧的 Task 3 JSON 和旧归档没有新增字段时仍可读取。只有实际存在分段时，归档清单
  才加入 `work_segments` / `segment_summaries`，以免改变旧的无轨迹指纹。
- 本任务不做候选学习节点、不做 AI 与 Git 的配对、不重放命令，也不改变既有隐私
  过滤层或四层证据模型。

## 测试边界

提取级测试使用人工合成事件覆盖：30 分钟边界、超过阈值、对象范围切换、缺失时间、
五类命令、稳定 ID、摘要引用和否认渲染。公开仓库没有 `107-contest` 与 `PY-ECO`
的原始会话文件，因此不能把真实样例重跑写成已验证事实；拿到经授权的真实文件后，
应由宿主 Agent 按现有授权流程运行并保存独立结果。
