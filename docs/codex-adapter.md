# Codex 轨迹适配器交接说明

## 输入方式

OpenAI Codex CLI 会把每个会话保存为本地 JSONL 文件（通常位于
`~/.codex/sessions/<日期>/rollout-<时间>-<uuid>.jsonl`）。LearnTrace 不会扫描或
读取该目录；使用者需要自己把要归档的会话文件复制出来，再逐个显式授权：

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
产物。事件在输出记录到达时才生成，因此状态是观察到的（输出中的退出码非零记为
"以错误结束"），而不是推测的；未观察到输出的调用按"未完成"跳过并写入安全警告。
超过 64 KiB 的调用参数不会进入 JSON 解析，直接使用通用摘要。

## 大小与数量上限

- 单个会话文件上限 256 MiB（解析为逐行流式，内存与文件大小无关）；
- 单行上限 16 MiB，超限行跳过并警告；末尾无换行的行视为可能仍在追加，能解析则
  容忍并警告；
- 单会话事件上限 2000 条，多宿主合并总量上限 10000 条，超出部分按时间保留最早
  事件并记录截断警告。

测试样例 `tests/fixtures/codex/authorized-session.jsonl` 是人工编写的数据，
不来自真实会话。
