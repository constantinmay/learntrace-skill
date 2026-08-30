# LearnTrace Skill

LearnTrace 是一个面向 AI 辅助课程项目的学习档案 Skill。它从本地 Git 仓库、项目文档、既有测试日志和用户授权的 AI 轨迹中整理可追溯证据，经学生确认后生成可编辑的 Markdown 学习档案。

项目不执行被分析仓库中的代码，不做作弊检测、作者归因、贡献排名或自动评分。

## 当前状态

项目已完成统一 CLI 与 OpenCode Skill 的隔离端到端联调：可以从项目仓库
生成 Task 2 证据、可选接入经授权的 Task 3 轨迹，并产出学习档案。详细范围
参见 [项目陈述](docs/project-statement.md) 和 [团队工作计划](docs/team-workplan.md)。

## 开发环境

- Python 3.11
- `uv`：Python 版本、虚拟环境、依赖与锁文件管理
- Ruff：代码检查与格式化
- Pyright：静态类型检查
- Pytest：测试
- pre-commit：提交前质量检查

安装依赖并运行检查：

```powershell
uv sync --dev --locked
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

## 用户安装

克隆发布版本后，在 LearnTrace 仓库中安装独立 CLI：

```powershell
uv tool install .
learntrace --version
```

然后将 `skills/learntrace/` 放入 OpenCode 的项目级
`.opencode/skills/learntrace/` 或全局 `~/.config/opencode/skills/learntrace/`。
只复制 Skill 目录不会自动安装 Python CLI。OpenCode 当前的 Skill 发现路径参见
[官方 Agent Skills 文档](https://opencode.ai/docs/skills)。

## 推荐使用方式

在待分析项目中启动 OpenCode，并要求它使用 `$learntrace`。Skill 会先检查 CLI，
再用一次 `learntrace discover` 展示候选 Git、文档和测试日志范围；学生确认前，
不应读取这些内容。若需要使用 OpenCode 会话导出，还必须单独提供文件并明确
授权。

OpenCode 使用的模型负责遵循 Skill 和调用本地 CLI。CLI 只使用本地确定性规则，
不调用远程模型，也不读取任何 `LEARNTRACE_*` 环境变量；更深入的语义理解由
宿主 Agent 承担（单 LLM 原则）。使用 `$learntrace` 不会自动授权上传
OpenCode 轨迹。

Skill 是宿主 Agent 的工作流约束，不是操作系统沙箱。实际联调已验证授权前
停止、单次归档和归档后优先提问；宿主模型仍可能产生多余的元数据查询，最终
的工具权限边界应由 OpenCode 配置和用户审批共同保证。

## 本地 CLI

从项目仓库运行完整本地流程：

```powershell
learntrace discover <project-dir>
learntrace run <project-dir>
```

`discover` 只列出候选文档、测试日志和 Git 是否可用，不读取文件内容。宿主
Agent 应先向学生展示该范围，确认后再运行完整解析。

Task 2 只使用本地 Git，不访问或依赖远程仓库。先生成完整、逐行可检索的轻量索引：

```powershell
learntrace git-index <project-dir>
```

历史索引中的每条提交包含 `tree_id`、版本文件数、顶层目录摘要、变更文件角色，
以及源码和测试是否在同一提交中出现的非因果关系。需要查看一个版本的完整文件
布局时再按需读取：

```powershell
learntrace git-tree <project-dir> <revision>
```

需要理解某次关键修改时，宿主 Agent 再按需导出本地原始证据：

```powershell
learntrace git-evidence <project-dir> <commit-hash> --path src/example.py
```

需要回读准确代码行，或检查尚未提交的开发过程时使用：

```powershell
learntrace git-file <project-dir> <revision> src/example.py --lines 120:180
learntrace git-file <project-dir> <revision> src/generated.txt --bytes 0:65535
learntrace git-file <project-dir> index src/example.py --lines 120:180
learntrace git-file <project-dir> worktree src/example.py --lines 120:180
learntrace git-worktree <project-dir>
```

`git-file` 每次最多读取 200 行或 1,000,000 字节，并记录 revision、路径、Git
object ID、实际范围、`reached_eof` 和下一页 continuation。限制的是单次返回量，
不是文件可访问范围；超长单行可改用 `--bytes` 继续读取。`index` 读取暂存区版本，
`worktree` 读取当前工作区版本。`git-worktree` 记录 staged、unstaged、untracked、删除、重命名和冲突，
但不会自动读取未跟踪文件内容；需要时仍由宿主 Agent 通过 `git-file ... worktree`
按范围读取。LearnTrace 自己生成的 `.learntrace/` 和 `learning-record.md` 不进入
工作区证据，避免工具输出被误当成用户开发过程。

命令在 `<project-dir>/.learntrace/evidence/git/<commit-hash>/` 写入 `index.json`
和有界 diff 预览。`index.json` 完整记录变更文件、对象 ID、diff hunk、前后 revision
和回读命令；源码正文由 `git-file` 按需读取，不再为每个文件复制完整前后版本。
该目录属于用户已授权的本地项目证据，不执行面向
公开分享的脱敏；应将 `.learntrace/` 加入目标项目的忽略规则，不要提交或直接分享。
二进制、非 UTF-8、Git LFS 指针和 submodule 不会被伪装成普通源码，索引会保留
路径、对象、大小和不可用原因，供最终档案如实说明证据缺口。

Git 历史默认完整读取，不使用任意条数上限。`--max-commits` 仅是调用方显式启用
的侧支细节预算；first-parent 主线和所有 merge commit 始终保留，被省略内容会
形成聚合事实和结构化统计。此时 `parse` 不会隐式生成完整历史索引；warning 中的
`history_index_status=requires_generation` 表示必须先执行其中给出的
`learntrace git-index <project>`，并核对索引 metadata 的 `head` 后再回读。
完整历史会随仓库提交数量增加运行时间和本地 `history.jsonl` 体积，但不会要求
Agent 一次读入全部内容；Agent 应先检索索引，再按提交和文件分页回读。

首次运行会生成 `learning-record.md`，并在 `<project-dir>/.learntrace/`
写入 Task 2、Task 3、机器可读档案和待确认问题。

三个主要输出用途不同：

- `learning-record.md`：供学生和老师阅读的精简档案，不倾倒全部工具日志。
- `.learntrace/archive-records.json`：保留完整事实、来源索引、候选关系、告警
  和内容指纹的机器审计档案。
- `.learntrace/learning-questions.md`：只保存需要学生本人确认、补充或否认
  的问题。

如需纳入 OpenCode 轨迹，首次运行时显式提供导出与授权：

```powershell
learntrace run <project-dir> `
  --opencode-export <opencode-export.json> --authorized
```

项目跨越多个 OpenCode 会话时，每个导出文件都必须分别提供并授权：

```powershell
learntrace run <project-dir> `
  --opencode-export <session-1.json> `
  --opencode-export <session-2.json> `
  --authorize-opencode-export <session-1.json> `
  --authorize-opencode-export <session-2.json>
```

多会话结果会合并并按事件去重，来源仍保留各自的 session 标识。未逐个授权
的导出不会被读取。旧的 `--authorized` 仅用于单个导出文件。

除 OpenCode 外，也支持经授权的 Claude Code 和 Codex 会话文件。使用者先从
`~/.claude/projects/` 或 `~/.codex/sessions/` 复制会话 JSONL（LearnTrace 不会扫描
这些目录），再逐个授权：

```powershell
learntrace run <project-dir> `
  --claude-code-export <session.jsonl> `
  --authorize-claude-code-export <session.jsonl> `
  --codex-export <rollout-session.jsonl> `
  --authorize-codex-export <rollout-session.jsonl>
```

三个宿主可以在同一次 `run` 中组合，事件合并进同一份机器档案并按稳定 ID 去重。
JSONL 会话采用逐行流式解析：单文件上限 256 MiB、单行 16 MiB、单会话事件 2000 条、
合并总量 10000 条，超出部分按时间保留最早事件并记录截断警告。详见
[Claude Code 适配器](docs/claude-code-adapter.md)和[Codex 适配器](docs/codex-adapter.md)。

学生填写独立确认文件后，第二次运行直接使用首次保存的事实和候选快照，
不会重新解析轨迹或调用 LLM：

```powershell
learntrace run <project-dir> `
  --confirmations student-confirmations.json
```

`--confirmations` 可重复传入；文件需包含非空 `confirmations` 列表。每条简写
记录需要 `candidate_id`、`decision` 和 RFC 3339 格式的 `confirmed_at`。
`student_statement` 只能填写学生原话；未提供原话时应省略，LearnTrace 会记录
显式的 `not_recorded`，不会替学生生成陈述。
确认阶段只复用首次分析生成的快照，不能同时传入 `--document`、`--test-log`、
Git 范围或 OpenCode 导出参数；这些组合会被 CLI 明确拒绝，而不会静默忽略。
候选状态 `resolved` 只表示用户已经处理该问题，具体结果仍由确认记录中的
`confirmed`、`supplemented` 或 `denied` 表示。

`.learntrace/archive-records.json` 包含用于审计的来源索引，应视为本地敏感产物；
对外分享前应检查已脱敏的 `learning-record.md`，不要直接上传机器归档。
`learning-record.md` 只展示项目目标、阶段进展、测试日志和候选直接引用的证据；
未被选中的文档事实不会为了显示脱敏占位符而进入主报告，完整记录仍保留在机器归档中。
OpenCode 原始导出还可能包含完整聊天和工具内容，也不得提交；隐私优先时可先用
`opencode export <sessionID> --sanitize` 生成脱敏导出，但其项目路径证据会相应减少。
Markdown 的路径识别采用隐私优先策略：明确的 `/api` 和版本化 `/v1` 一类接口路由
会保留，其他以 `/` 开头且无法可靠区分用途的文本（例如 `/health`、`/docs/x`）
可能按绝对路径脱敏。完整来源只保存在本地机器归档中。

`parse`、`adapt` 和 `archive` 也可以分别运行。读取 OpenCode 导出时必须对
`adapt` 或 `run` 同时传入 `--authorized`；LearnTrace 不会执行目标项目代码、
测试或日志中的命令。分阶段确认时，应对 `archive` 同时传入首次生成的
`--snapshot archive-records.json`，避免重新推断候选。

已有 Task 2、Task 3 JSON 时，不需要复制或重新解析输入。从项目根目录运行：

```powershell
New-Item -ItemType Directory -Force .learntrace | Out-Null
learntrace archive <records-dir> `
  --output learning-record.md `
  --records-output .learntrace/archive-records.json `
  --questions-output .learntrace/learning-questions.md
```

命令成功后直接读取 `.learntrace/learning-questions.md`，不要重复执行首次归档。
确认阶段对同一个 `records-dir` 使用 `.learntrace/archive-records.json` 快照。

CLI 默认使用确定性候选推断器。归档扫描会跳过 `.opencode`、`.venv`、`.git`
和 `node_modules` 等噪音目录；只有确认输入树中的每个 JSON 都应是 LearnTrace
产物时，才对 `archive` 使用 `--strict-inputs`。机器可读档案包含带稳定
SHA-256 指纹的审计清单，CLI 同时输出该指纹与记录数量。
候选推断是本地确定性规则：CLI 不调用远程 LLM，也不读取任何 `LEARNTRACE_*`
环境变量；更深入的语义理解由宿主 Agent 承担（单 LLM 原则）。即使没有形成
候选，待确认问题文件也会提供基于证据缺口的学生复盘问题，而不会编造候选。

## 目录结构

```text
src/learntrace/        Python 包与 CLI
src/learntrace/schemas/v0/  随 Python 包发布的 schema v0 数据契约
skills/learntrace/     可安装 Skill
tests/unit/            单元测试
tests/contract/        Schema 与金标样例回归
tests/integration/     跨模块联调测试
tests/fixtures/        脱敏测试材料
examples/              可公开演示项目
docs/                  项目说明与架构文档
local/                 不进入 Git 的本地材料
```

开始编写 Python 功能前，请先阅读 [`src/README.md`](src/README.md) 中的模块边界和开发示例。

## 分支与协作

`main` 是唯一长期分支，禁止直接推送。每项工作从最新 `main` 创建短分支，通过 PR 审核和测试后合并并删除。

初始任务分支建议：

- `feature/schema-v0`
- `feature/static-parser`
- `feature/opencode-adapter`
- `feature/portfolio-skill`

更多约定参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 隐私

公开内容只能使用脱敏样例。密钥、个人信息、真实学生材料、完整 AI 对话和本地基础设施文档不得提交。`local/` 目录始终由 Git 忽略。
