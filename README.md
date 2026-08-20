# LearnTrace Skill

LearnTrace 是一个面向 AI 辅助课程项目的学习档案 Skill。它从本地 Git 仓库、项目文档、既有测试日志和用户授权的 AI 轨迹中整理可追溯证据，经学生确认后生成可编辑的 Markdown 学习档案。

项目不执行被分析仓库中的代码，不做作弊检测、作者归因、贡献排名或自动评分。

## 当前状态

项目已进入端到端联调阶段：统一 CLI 可以从项目仓库生成 Task 2 证据、可选接入经授权的 Task 3 轨迹，并产出 Task 4 学习档案。详细范围参见 [项目陈述](docs/project-statement.md) 和 [团队工作计划](docs/team-workplan.md)。

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

## 本地 CLI

从项目仓库运行完整本地流程：

```powershell
uv run --locked python -m learntrace run <project-dir>
```

首次运行会生成 `learning-record.md`，并在 `<project-dir>/.learntrace/`
写入 Task 2、Task 3、机器可读档案和待确认问题。学生填写独立确认文件后，
使用稳定的候选 ID 再运行一次：

```powershell
uv run --locked python -m learntrace run <project-dir> `
  --confirmations student-confirmations.json
```

`--confirmations` 可重复传入；文件需包含非空 `confirmations` 列表。每条简写
记录需要 `candidate_id`、`decision` 和 RFC 3339 格式的 `confirmed_at`。
`student_statement` 只能填写学生原话；未提供原话时应省略，LearnTrace 会记录
显式的 `not_recorded`，不会替学生生成陈述。

`parse`、`adapt` 和 `archive` 也可以分别运行。读取 OpenCode 导出时必须对
`adapt` 或 `run` 同时传入 `--authorized`；LearnTrace 不会执行目标项目代码、
测试或日志中的命令。

CLI 默认使用确定性候选推断器。归档扫描会跳过 `.opencode`、`.venv`、`.git`
和 `node_modules` 等噪音目录；只有确认输入树中的每个 JSON 都应是 LearnTrace
产物时，才对 `archive` 使用 `--strict-inputs`。机器可读档案包含带稳定
SHA-256 指纹的审计清单，CLI 同时输出该指纹与记录数量。

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
