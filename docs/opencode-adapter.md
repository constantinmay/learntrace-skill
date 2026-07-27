# OpenCode 轨迹适配器交接说明

## 输入方式

使用者先在 OpenCode 中导出指定会话：

```shell
opencode export <sessionID> > opencode-session.json
```

调用方必须把这个 JSON 文件的路径传给 `adapt_opencode_export`，并通过
`authorized=True` 明确表示已经获得读取授权。适配器不会搜索 OpenCode 的本地数据库，
也不会自动寻找其他会话。`authorized=False` 时，它会在检查文件是否存在之前直接返回。

## 四种结果状态

- `not_provided`：已经授权，但调用方没有提供文件路径。
- `not_authorized`：没有获得读取授权，适配器不会访问输入路径。
- `authorized_not_found`：文件不存在，或有效导出里没有可使用的工具记录。
- `parsed`：至少生成了一条轨迹事件；个别坏记录会被跳过并写入安全警告。

Task 3 的批次结果包含 `status`、`events` 和 `warnings`。其中只有 `events` 是提供给
Task 4 的稳定跨任务接口。

## 会保留什么

每条可用的工具记录会变成一个 Schema v0 `ObservableEvent`：

- `kind` 固定为 `trace_record`；
- `summary` 只保留工具名、项目内相对路径或保守的命令类型；
- `occurred_at` 只取工具记录的开始时间，并且可以省略；
- `source_refs` 指向原会话、消息和工具片段的标识，`note` 为 `opencode`。

写入结果时，`write_trace_result` 会再次校验所有事件，再用同目录临时文件和原子替换
生成 UTF-8 JSON。

## 会删除什么

适配器不保存聊天、推理、提示词、Task 的 `description`、`subagent_type`、`prompt`、
工具输出、错误正文、完整命令、密钥或个人绝对路径。测试样例
`tests/fixtures/opencode/authorized-export.json` 是人工编写的数据，不来自真实会话。

适配器不会执行或重放轨迹中的命令，不生成学习候选，不向学生提问，也不会把 OpenCode
轨迹和 Git 提交推断成已经确定的对应关系。
