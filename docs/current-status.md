# LearnTrace 当前状态与决策总结

更新时间：2026-07-17

本文用于在项目根目录重命名、重新打开工作区或更换开发会话后恢复上下文。它记录当前已经完成的仓库初始化、产品方向讨论和下一阶段待办。

## 项目名称

- 当前工作目录：`E:\cs_and_ai\107`
- 计划目录名称：`learntrace-skill`
- 计划仓库名称：`learntrace-skill`
- Python 包和 CLI 名称：`learntrace`
- GitHub 账号候选：`constantinmay`
- GitHub 仓库尚未创建
- 仓库可见性建议：开发阶段先设为私有，完成脱敏、许可证和安全检查后再决定是否公开

## 产品定位

LearnTrace 面向使用 AI 完成课程项目的学生，将项目证据、AI 协作过程和学生确认整理成可追溯的学习档案。

系统不做：

- 作弊检测
- AI 作者归因
- 成员贡献排名
- 学习程度自动评分
- 执行被分析项目的代码或测试
- 将系统推断直接写成学生已经掌握的事实

## 当前推荐的产品流程

经过讨论，当前不优先开发普通 ZIP 上传分析，也不立即实现持续运行的并行观察 Agent。推荐采用由学生主动控制的检查点式流程：

```text
learntrace init
      ↓
建立项目基线、证据范围和隐私规则
      ↓
学生正常与 AI 协作开发
      ↓
learntrace reflect
      ↓
分析阶段变化并让学生确认学习节点
      ↓
learntrace report
      ↓
汇总生成学习档案
```

### `learntrace init`

行为类似编码工具的项目初始化命令，但目标不是单纯指导 Agent 编程，而是为后续学习证据记录建立基线。

计划分析：

- 项目目标和现有说明文档
- 技术栈、包管理和目录结构
- Git 状态和可用历史
- 构建、测试和质量检查配置
- 可用证据来源
- 应排除的隐私路径

计划产物：

```text
LEARNTRACE.md
.learntrace/project.json
```

### `learntrace reflect`

由学生在一个阶段结束时主动触发，读取上次复盘之后的 Git 变化、已有测试结果和用户明确授权的会话信息，提出少量候选学习节点。学生可以确认、补充或否认。

### `learntrace report`

汇总已经确认的阶段记录，生成可编辑的 Markdown 学习档案和可选的脱敏分享版。

## 实时跟踪的后续扩展

实时跟踪不需要重写整个项目。推荐继续复用当前 Python 核心，并增加宿主集成层：

```text
成熟编码 Agent
├─ LearnTrace Skill：init、reflect、report
├─ LearnTrace Plugin/Hook：捕获生命周期事件
└─ LearnTrace Python 核心：存储、分析和报告
```

职责划分：

- Skill：用户主动调用的交互工作流
- Hook：确定性记录用户请求、工具调用结果、文件变化和会话节点
- Plugin：将 Skill、Hook 和安装配置打包分发
- 观察 Agent：只在检查点理解新增事件，不负责原始事实记录

首个宿主仍建议选择 OpenCode；后续可增加 Claude Code 适配。实时记录应作为后续能力，不阻塞检查点式 MVP。

## 避免重复开发的架构原则

所有输入最终转换成统一事件，不为 ZIP、历史记录和实时事件分别编写独立分析系统：

```text
项目初始化发现 ───────┐
Git 与文件证据 ───────┤
历史事件包导入 ───────┼─> 统一事件与证据模型 ─> 学习节点 ─> 报告
OpenCode 实时事件 ─────┤
Claude Code 实时事件 ──┘
```

普通 ZIP 最多作为项目证据包的导入、导出或回放容器，不作为独立产品主线。

## 证据规则

每条学习档案内容必须保持以下分类：

1. 可观察事实：可直接追溯到 Git、文件、文档、既有测试日志或授权轨迹。
2. 学生确认：学生对候选学习节点的确认、补充或否认。
3. 系统候选推断：必须附依据和不确定性，未经确认不能成为学习结论。

证据不足时标记“未记录”，不补全事件，也不推断 AI 使用情况。

`schema v0` 的具体字段、类型和枚举仍需四名成员共同讨论后冻结。当前 `schemas/v0/README.md` 只是占位说明，不是正式 Schema。

## 四人建议分工

| 任务线 | 主要目录 |
| --- | --- |
| 数据契约和金标样例 | `schemas/`、`src/learntrace/models/`、`tests/contract/`、`tests/fixtures/` |
| Git、文件和测试证据采集 | `src/learntrace/parsers/` 及对应单元测试 |
| OpenCode 适配和隐私过滤 | `src/learntrace/adapters/`、`src/learntrace/privacy/` 及对应单元测试 |
| Skill、初始化、复盘和报告 | `skills/`、`src/learntrace/cli.py`、`src/learntrace/reporting/`、集成测试 |

目录是主要维护责任，不是绝对修改权限。跨模块公共接口变化需要相关负责人共同审核。

## Git 协作方式

- `main` 是唯一长期分支
- 不建议建立个人长期分支或额外的 `develop`
- 一项 Issue 对应一个短分支和一个 PR
- 合并后删除短分支
- 推荐 Squash merge

初始任务分支在成员领取任务时再创建：

```text
feature/schema-v0
feature/project-init
feature/opencode-adapter
feature/reflection-report
```

## 已完成的仓库初始化

- 已初始化 Git，默认分支为 `main`
- 已建立 Python `src` 布局
- 已使用 Python 3.11 和 `uv`
- 已生成并提交 `uv.lock`
- 已配置 Ruff、Pyright、Pytest 和 pre-commit
- 已创建 LearnTrace Skill 骨架并通过结构校验
- 已创建 README、贡献指南、架构说明、Issue 模板和 PR 模板
- 已将原项目陈述和团队工作计划移入 `docs/`
- 已将 SSH 操作文档移入 `local/` 并确认被 Git 忽略
- 未添加 VS Code 专用配置
- 尚未创建 GitHub 远程仓库
- 尚未创建初始功能分支

当前主要提交：

```text
e0c69b4 chore:init-learntrace-project
1e06a2b docs:add-src-development-guide
```

## 当前验证状态

最近一次初始化验证结果：

- Ruff：通过
- Ruff 格式检查：通过
- Pyright：0 个错误
- Pytest：1 个测试通过
- CLI：`learntrace 0.1.0`
- Skill 结构校验：通过

## 改名后的继续步骤

1. 关闭占用当前目录的 Codex、终端或编辑器进程。
2. 将 `E:\cs_and_ai\107` 重命名为 `E:\cs_and_ai\learntrace-skill`。
3. 从新目录重新打开项目。
4. 阅读本文、根目录 `README.md` 和 `src/README.md` 恢复上下文。
5. 运行 `git status`，确认工作区干净。
6. 决定是否以 `constantinmay/learntrace-skill` 创建私有 GitHub 仓库。
7. 推送 `main` 后配置分支保护，再按 Issue 创建短功能分支。

## 下一阶段优先任务

建议先通过 Issue 明确 `learntrace init` 的输入、只读发现规则和输出格式，然后实现最小闭环：

```text
扫描当前仓库
  → 生成项目基线草稿
  → 用户确认
  → 写入 LEARNTRACE.md 和 .learntrace/project.json
```

在这条流程稳定之后，再讨论 `reflect` 的事件格式和 OpenCode Hook。
