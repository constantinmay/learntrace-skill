# Claude Code 轨迹适配器交接说明

## 两种能力不要混淆

LearnTrace 可以作为 Claude Code Skill 直接运行，也可以在用户授权后把 Claude Code
历史会话作为学习证据导入。这是两种相互独立的能力：

- 直接运行：把 `skills/learntrace/` 安装到项目级
  `.claude/skills/learntrace/` 或个人级 `~/.claude/skills/learntrace/`，确保
  `learntrace` CLI 在 `PATH` 中，然后在项目内输入 `/learntrace`；
- 导入历史：仅当用户希望档案反映 AI 协作过程时，才提供会话 JSONL，并对每个
  文件单独授权。

调用 `/learntrace` 本身不构成历史会话授权，Skill 也不会自动扫描 Claude Code
的历史目录。完整安装和直用步骤见根目录 [README](../README.md)。

## 输入方式

Claude Code 会把每个会话保存为本地 JSONL 文件（通常位于
`~/.claude/projects/<项目目录>/<会话>.jsonl`）。LearnTrace 不会扫描或读取该目录；
使用者需要自己把要归档的会话文件复制出来，再逐个显式授权：

```powershell
learntrace run <project-dir> `
  --claude-code-export <session.jsonl> `
  --authorize-claude-code-export <session.jsonl>
```

多个会话时重复传参，每个文件单独授权；也可以用独立 `adapt` 子命令：

```powershell
learntrace adapt <session-1.jsonl> <session-2.jsonl> `
  --source claude-code `
  --authorize-export <session-1.jsonl> `
  --authorize-export <session-2.jsonl>
```

原始会话文件包含完整聊天、工具输出和文件内容，属于敏感临时文件，不要提交或分享。
`--claude-code-export` 存在但未授权时返回 `not_authorized`，适配器不会访问该路径。

## 四种结果状态

与 OpenCode 适配器一致：`not_provided`、`not_authorized`、`authorized_not_found`、
`parsed`。

成功结果还会附带可选的 `work_segments`，用于把原子 `trace_record` 按时间和项目对象
范围整理成连续工作过程；这是 Task 3 的附加索引，不会替代 `events`。统一字段见
[AI 轨迹分段说明](trace-segmentation.md)。

## 会保留什么

- `tool_use` 与后续 `tool_result` 配对成功的调用，各生成一条 Schema v0
  `ObservableEvent`（`kind` 为 `trace_record`，`note` 为 `claude-code`）；
- `summary` 只保留工具名、项目内相对路径（Read/Write/Edit 类）或保守的命令类型
  （Bash），结尾统一脱敏；
- `occurred_at` 取工具调用行的时间戳，可省略；
- 会话标识取自记录的 `sessionId`，缺失时回退文件名，进入 `trace://claude-code/...`
  来源索引。

## 会删除什么

聊天、思考内容、工具输出正文、命令完整参数、文件内容、密钥和个人绝对路径一律
不进入产物。事件在 `tool_result` 到达时才生成，因此状态是观察到的，而不是推测的；
未观察到结果的调用按"未完成"跳过并写入安全警告。

## 大小与数量上限

- 单个会话文件上限 256 MiB（逐行流式解析，不整读文件）；
- 单行上限 16 MiB，超限行跳过并警告；末尾无换行的行视为可能仍在追加，但它与其他
  行一样先经过大小与标记检查，通过后才容忍解析并警告；
- 解析阶段的累积同样有上限：单会话事件达到 2000 条后不再构造新事件，警告最多保留
  200 条（超出部分汇总为一条），未配对调用跟踪达到 2000 条后不再积累；多宿主合并
  总量上限 10000 条。各上限触发时均记录汇总警告（`event_cap_reached` /
  `tracked_call_cap_reached` / `warning_cap_reached`），且汇总警告不占用 200 条
  详细警告配额，即使详细警告超限被丢弃也始终保留。

测试样例 `tests/fixtures/claude-code/authorized-session.jsonl` 是人工编写的数据，
不来自真实会话。
