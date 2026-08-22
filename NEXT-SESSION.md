# LearnTrace 下一会话交接

更新时间：2026-08-20

## 当前可运行流程

当前仓库实现的是统一的一次性本地流水线，不是 `init → reflect → report`
状态机。裸项目仓库的可复现入口为：

```powershell
uv sync --locked
uv run --locked python -m learntrace run <project-dir>
```

该命令调用 `parse → 可选 adapt → archive`，但只读取 Git、文档、已有测试
日志和显式授权的 AI 轨迹导出，不执行被分析项目、测试或日志命令。
支持三种轨迹宿主：OpenCode（JSON 导出）、Claude Code 与 Codex（JSONL 会话，
使用者从 `~/.claude/projects/` 或 `~/.codex/sessions/` 复制后逐个授权）。
`parse`、`adapt`、`archive` 也可分别运行（`adapt` 通过 `--source` 选择宿主）。

首次运行生成待确认问题。学生填写确认文件后，可再次运行：

```powershell
uv run --locked python -m learntrace run <project-dir> `
  --confirmations student-confirmations.json
```

默认使用确定性候选推断器。只有同时设置
`LEARNTRACE_LLM_ENABLED=1` 和 `LEARNTRACE_LLM_API_KEY` 时才启用远程
LLM 候选推断；出站字段和隐私边界以 `skills/learntrace/SKILL.md` 为准。

## 当前产物

- `learning-record.md`：可编辑学习档案。
- `archive-records.json`：带稳定指纹和来源索引的机器可读档案。
- `learning-questions.md`：尚待学生确认的问题。
- `.learntrace/task2-result.json`：Task 2 静态证据。
- `.learntrace/task3-result.json`：Task 3 授权轨迹或未授权状态（可合并多个宿主）。

## 维护原则

- 可观察事实、候选推断和学生确认必须分层。
- 学生没有提供的目标、反思或后续计划显示为“未记录”，不得代写。
- AI 使用章节只展示项目范围内、有学习语义的授权轨迹（OpenCode、Claude Code
  或 Codex 均可）。
- 所有功能修改运行 Ruff、Pyright、Pytest 和 pre-commit。

## 未来路线（尚未实现）

`learntrace init`、`learntrace reflect`、`learntrace report`、持续 Hook 和
实时 Agent 都属于未来路线。文档不得把这些命令描述为当前可用功能。
