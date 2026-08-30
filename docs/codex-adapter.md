# Codex 轨迹适配器交接说明

## 输入方式

OpenAI Codex CLI 会把每个会话保存为本地 JSONL 文件（通常位于
`~/.codex/sessions/<年/月/日>/rollout-<时间>-<uuid>.jsonl`；归档后移至
`~/.codex/archived_sessions/`）。LearnTrace 不会扫描或
读取这些目录；使用者需要自己把要归档的会话文件复制出来，再逐个显式授权：

```powershell
learntrace run <project-dir> `
  --codex-export <rollout-session.jsonl> `
  --authorize-codex-export <rollout-session.jsonl>
```

多个会话时重复传参，每个文件单独授权；也可以用独立 `adapt` 子命令：

```powershell
learntrace adapt <session-1.jsonl> <session-2.jsonl> `
  --source codex `
  --authorize-export <session-1.jsonl> `
  --authorize-export <session-2.jsonl>
```

原始会话文件包含完整聊天、工具输出和补丁内容，属于敏感临时文件，不要提交或分享。
`--codex-export` 存在但未授权时返回 `not_authorized`，适配器不会访问该路径。

## 四种结果状态

与 OpenCode 适配器一致：`not_provided`、`not_authorized`、`authorized_not_found`、
`parsed`。

成功结果还会附带可选的 `work_segments`，用于把原子 `trace_record` 按时间和项目对象
范围整理成连续工作过程；这是附加索引，不会替代 `events`。统一字段和宿主 Agent
摘要授权规则见[AI 轨迹分段说明](trace-segmentation.md)。

## 会保留什么

- `function_call` / `local_shell_call` 与后续 `function_call_output` 配对成功的调用，
  各生成一条 Schema v0 `ObservableEvent`（`kind` 为 `trace_record`，`note` 为
  `codex`）；
- `summary` 只保留工具名和保守的命令类型（从调用参数中提取可执行文件与子命令），
  结尾统一脱敏；
- `occurred_at` 取调用行的时间戳，可省略；
- 会话标识优先取 `session_meta` 的 `id`，缺失时回退文件名，进入
  `trace://codex/...` 来源索引。

## 会删除什么

聊天、推理、函数输出正文、补丁内容、命令完整参数、密钥和个人绝对路径一律不进入
产物。事件在输出记录到达时才生成，因此状态是观察到的，而不是推测的：输出中的
退出码非零（JSON `metadata.exit_code`、顶层 `exit_code` 或文本形式如
"Exit code: 1"）记为"以错误结束"；无法确认退出状态时记为"结束（状态未知）"，
不会默认为成功；未观察到输出的调用按"未完成"跳过并写入安全警告。
超过 64 KiB 的调用参数不会进入 JSON 解析，直接使用通用摘要。

## 支持范围

针对 Codex CLI 0.149 的会话格式验证（`session_meta`、`response_item` 中的
`function_call` / `local_shell_call` / `function_call_output`）。识别到
`custom_tool_call` / `custom_tool_call_output` 时会记入
`unsupported_call_type` 警告并跳过，其余记录类型的适配列为后续任务。

## 大小与数量上限

- 单个会话文件上限 256 MiB（逐行流式解析，不整读文件）；
- 单行上限 16 MiB，超限行跳过并警告；末尾无换行的行视为可能仍在追加，但它与其他
  行一样先经过大小与标记检查，通过后才容忍解析并警告；
- 解析阶段的累积同样有上限：单会话事件达到 2000 条后不再构造新事件，警告最多保留
  200 条（超出部分汇总为一条），未配对调用跟踪达到 2000 条后不再积累；多宿主合并
  总量上限 10000 条。各上限触发时均记录汇总警告（`event_cap_reached` /
  `tracked_call_cap_reached` / `warning_cap_reached`），且汇总警告不占用 200 条
  详细警告配额，即使详细警告超限被丢弃也始终保留。

测试样例 `tests/fixtures/codex/authorized-session.jsonl` 是人工编写的数据，
不来自真实会话。
