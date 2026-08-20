# LearnTrace 下一会话交接

更新时间：2026-08-20

## 当前可运行流程

当前仓库实现的是一次性本地归档流程，不是 `init → reflect → report`
状态机。可复现入口为：

```powershell
uv sync --locked
uv run --locked python -m learntrace.archive <records-dir> `
  --output learning-record.md `
  --records-output archive-records.json `
  --questions-output learning-questions.md
```

输入目录包含 Task 2 解析结果、完整 LearnTrace 记录或授权后的 Task 3
适配结果。CLI 只读取本地 JSON，不执行被分析项目、测试或日志命令。

默认使用确定性候选推断器。只有同时设置
`LEARNTRACE_LLM_ENABLED=1` 和 `LEARNTRACE_LLM_API_KEY` 时才启用远程
LLM 候选推断；出站字段和隐私边界以 `skills/learntrace/SKILL.md` 为准。

## 当前产物

- `learning-record.md`：可编辑学习档案。
- `archive-records.json`：带稳定指纹和来源索引的机器可读档案。
- `learning-questions.md`：尚待学生确认的问题。

## 维护原则

- 可观察事实、候选推断和学生确认必须分层。
- 学生没有提供的目标、反思或后续计划显示为“未记录”，不得代写。
- AI 使用章节只展示项目范围内、有学习语义的授权轨迹。
- 所有功能修改运行 Ruff、Pyright、Pytest 和 pre-commit。

## 未来路线（尚未实现）

`learntrace init`、`learntrace reflect`、`learntrace report`、持续 Hook 和
实时 Agent 都属于未来路线。文档不得把这些命令描述为当前可用功能。
