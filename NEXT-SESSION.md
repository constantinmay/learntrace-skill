# LearnTrace 下一会话交接

更新时间：2026-07-17

## 唯一工作目录

    E:\cs_and_ai\LearnTrace-Skill

不要在原路径 E:\cs_and_ai\107 或其他目录修改项目文件。

## 当前状态

- Git 已初始化，当前分支为 main，工作区在创建本文前是干净的。
- GitHub 远程仓库和功能分支尚未创建。
- 仓库名计划使用 learntrace-skill，Python 包与 CLI 使用 learntrace。
- 使用 Python 3.11、uv、Ruff、Pyright、Pytest 和 pre-commit。
- uv.lock 已生成。
- 根目录改名后，旧 .venv 已删除，并使用 uv sync --dev --locked 在新路径成功重建。
- SSH 操作文档位于 local 目录并被 Git 忽略。
- 未添加 VS Code 专用配置。

## 当前产品决策

MVP 不以普通 ZIP 上传分析为主线，也不立即实现持续运行的实时并行 Agent。

推荐流程：

    learntrace init
        -> 学生正常开发
        -> learntrace reflect
        -> learntrace report

init 只读分析当前仓库，经用户确认分析范围和隐私排除项后，生成项目指南与结构化基线。

reflect 由学生在阶段节点主动触发，整理上次检查点以来的 Git、文件、既有测试和授权轨迹证据，提出少量候选学习节点，并由学生确认、补充或否认。

report 只汇总经过学生确认的阶段记录，生成本地 Markdown 学习档案。

计划产物：

    LEARNTRACE.md
    .learntrace/project.json
    .learntrace/journal/
    learning-portfolio.md

## 未来实时集成

实时跟踪不需要重写项目。Python 核心继续负责统一事件、证据和报告；Skill 提供 init、reflect、report；Hook 确定性捕获生命周期事件；Plugin 将它们集成到 OpenCode，后续再适配 Claude Code。

原则是：Hook 记录事实，Agent 理解和提问，学生最终确认。实时 Hook 属于后续扩展，不阻塞检查点式 MVP。

## 证据与隐私

- 可观察事实必须引用 Git、文件、文档、既有日志或授权轨迹。
- 学生确认必须与系统候选分开保存。
- 系统候选推断必须附依据和不确定性，未经确认不得成为结论。
- 证据不足时使用“未记录”，不得补全事件。
- 不执行被分析仓库中的代码、测试或日志命令。
- 完整 AI 对话只有在学生明确授权时才能读取。

## 四人建议分工

1. 数据契约：schemas、models、contract tests、fixtures。
2. 项目建档：项目发现、parsers、init 流程。
3. 检查点证据：reflection、privacy、未来 adapters。
4. Skill 与报告：skills、CLI、reporting、integration tests。

schema v0 的正式字段、类型和枚举仍需四人讨论后冻结。当前 schemas/v0/README.md 只是占位说明。

## 下一会话待办

现有 README、docs/project-statement.md、docs/architecture.md、docs/team-workplan.md 和 skills/learntrace/SKILL.md 仍主要描述旧路线，尚未统一到 init -> reflect -> report。

下一会话应先阅读上述文件、src/README.md 和本文，然后：

1. 只在新的项目根目录工作。
2. 将产品文档和 Skill 统一到 init、reflect、report。
3. 暂不实现普通 ZIP、Web 平台或持续实时监听。
4. 运行 Ruff、格式检查、Pyright、Pytest 和 Skill 校验。
5. 提交文档统一变更。
6. 经用户确认后再创建私有 GitHub 远程仓库和分支保护。

验证命令：

    uv sync --dev --locked
    uv run ruff check .
    uv run ruff format --check .
    uv run pyright
    uv run pytest
