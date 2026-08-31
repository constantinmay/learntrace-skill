# OpenCode 轨迹适配器交接说明

## 输入方式

使用者先在 OpenCode 中导出指定会话：

```shell
opencode export <sessionID> --sanitize > opencode-session.json
```

`--sanitize` 会先由 OpenCode 脱敏聊天和文件内容，适合隐私优先的归档；它也可能
去掉工具输入中的项目内路径，使轨迹只保留工具类型、状态和时间。如果确实需要
项目内相对路径作为证据，可以在本地使用普通导出，但应把原始导出视为敏感临时
文件，不要提交或分享；LearnTrace 仍只会输出白名单摘要。

调用方必须把这个 JSON 文件的路径传给 `adapt_opencode_export`，并通过
`authorized=True` 明确表示已经获得读取授权。适配器不会搜索 OpenCode 的本地数据库，
也不会自动寻找其他会话。`authorized=False` 时，它会在检查文件是否存在之前直接返回。

跨多个会话时，可以调用 `adapt_opencode_exports`，并在 `authorized_paths` 中逐个
列出获准读取的路径；授权匹配发生在打开文件之前。CLI 对应命令为：

```powershell
learntrace adapt <session-1.json> <session-2.json> `
  --authorize-export <session-1.json> `
  --authorize-export <session-2.json>
```

各会话事件会合并、排序并按稳定事件 ID 去重，`source_refs` 中仍保留原 session。
任一会话的授权不会扩展到其他导出文件。

## 四种结果状态

- `not_provided`：已经授权，但调用方没有提供文件路径。
- `not_authorized`：没有获得读取授权，适配器不会访问输入路径。
- `authorized_not_found`：文件不存在，或有效导出里没有可使用的工具记录。
- `parsed`：至少生成了一条轨迹事件；个别坏记录会被跳过并写入安全警告。

Task 3 的批次结果包含 `status`、`events` 和 `warnings`。其中只有 `events` 是提供给
Task 4 的稳定跨任务接口；`work_segments` 是在不改变原子事件的前提下新增的确定性
分段视图。分段规则和字段见
[AI 轨迹分段说明](trace-segmentation.md)。

适配器成功解析后会在结果中附带 `work_segments`。它只引用同一结果里的事件 ID，按
默认 30 分钟（严格大于才切段）和明确项目对象范围切分；没有有效时间或安全路径时
不会猜测边界。旧调用方只读取 `events` 也不受影响。

## 会保留什么

每条可用的工具记录会变成一个 Schema v0 `ObservableEvent`：

- `kind` 固定为 `trace_record`；
- `summary` 只保留工具名、项目内相对路径或保守的命令类型；
- `occurred_at` 只取工具记录的开始时间，并且可以省略；
- `source_refs` 指向原会话、消息和工具片段的标识，`note` 为 `opencode`。

适配器读取授权导出文件时使用调用方给出的真实路径，但事件里的辅助文件引用只保留
项目内相对路径；项目外或未提供项目根时改用 `[outside-project]` / `[absolute-path]`
占位符，不把主机用户名或 home 路径写进轨迹结果。真正的轨迹定位仍以
`type=trace_record` 的会话、消息和工具片段引用为准。

写入结果时，`write_trace_result` 会再次校验所有事件，再用同目录临时文件和原子替换
生成 UTF-8 JSON。

## 会删除什么

适配器不保存聊天、推理、提示词、Task 的 `description`、`subagent_type`、`prompt`、
工具输出、错误正文、完整命令、密钥或个人绝对路径。测试样例
`tests/fixtures/opencode/authorized-export.json` 是人工编写的数据，不来自真实会话。

适配器不会执行或重放轨迹中的命令，不生成学习候选，不向学生提问，也不会把 OpenCode
轨迹和 Git 提交推断成已经确定的对应关系。
