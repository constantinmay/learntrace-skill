# LearnTrace Skill

LearnTrace 是一个面向 AI 辅助课程项目的学习档案 Skill。它从本地 Git 仓库、项目文档、既有测试日志和用户授权的 AI 轨迹中整理可追溯证据，经学生确认后生成可编辑的 Markdown 学习档案。

项目不执行被分析仓库中的代码，不做作弊检测、作者归因、贡献排名或自动评分。

## 当前状态

项目处于 M1 初始化阶段，当前目标是跑通一条可信学习节点的端到端闭环。详细范围参见 [项目陈述](docs/project-statement.md) 和 [团队工作计划](docs/team-workplan.md)。

## 开发环境

- Python 3.11
- `uv`：Python 版本、虚拟环境、依赖与锁文件管理
- Ruff：代码检查与格式化
- Pyright：静态类型检查
- Pytest：测试
- pre-commit：提交前质量检查

安装依赖并运行检查：

```powershell
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

## Task 4 local CLI

Build a Markdown learning archive from local LearnTrace JSON records:

```powershell
python -m learntrace.archive <project-dir> `
  --output learning-record.md `
  --records-output archive-records.json `
  --questions-output learning-questions.md
```

The CLI uses the deterministic stub inferencer by default. It skips common noise
directories such as `.venv`, `.git`, and `node_modules`; pass `--strict-inputs`
when every JSON file in the input tree is expected to be a LearnTrace artifact.
The machine-readable archive contains an audit manifest with a stable SHA-256
fingerprint, and the CLI prints the same fingerprint plus record counts.

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
