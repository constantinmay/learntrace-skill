# LearnTrace

从本地 Git、项目文档、既有测试日志，以及**明确授权**的 AI 会话中，整理可追溯的学习档案。

学生确认后，生成可编辑的 Markdown 阅读稿。工具不执行被分析仓库里的代码，也不做作弊检测、作者归因、贡献排名或自动评分。

需要：Python 3.11+ 和 [`uv`](https://docs.astral.sh/uv/)。

## 安装

在本仓库根目录安装 CLI：

```powershell
uv tool install .
learntrace --version
```

更新时在新的发布目录再执行 `uv tool install --reinstall .`。

然后把完整的 `skills/learntrace/`（含 `references/`）放到所用宿主的 Skill 目录。只复制 `SKILL.md`，或只安装 Skill，都不会带上 CLI。

| 宿主 | 项目级 | 用户级 | 调用 |
| --- | --- | --- | --- |
| Claude Code | `.claude/skills/learntrace/` | `~/.claude/skills/learntrace/` | `/learntrace` |
| OpenCode | `.opencode/skills/learntrace/` | `~/.config/opencode/skills/learntrace/` | `$learntrace` |

发现规则见 [Claude Code Skills](https://code.claude.com/docs/en/skills) 与 [OpenCode Agent Skills](https://opencode.ai/docs/skills)。

两种入口：

1. **宿主 Agent + Skill + CLI**。模型、登录和审批由 Claude Code / OpenCode / Codex 负责。只要 Python 3.11+ 和 `learntrace`，不需要本仓库的 Web 界面、Pi CLI 或 Node.js。
2. **`learntrace ui`**。可选的本机网页。需要 Node.js 22.22.2 或更高版本；页面和 Agent 服务已打进 Python 包，不必再 `npm install`。

## Claude Code

Windows 上安装到个人目录（所有项目可用）：

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\skills\learntrace"
Copy-Item ".\skills\learntrace\*" `
  "$env:USERPROFILE\.claude\skills\learntrace" -Recurse -Force
```

也可以只复制到待分析项目的 `.claude/skills/learntrace/`。新建 `.claude/skills/` 后若没有立刻出现 Skill，重启一次 Claude Code。

在待分析项目里：

```text
/learntrace 请分析当前项目中的开发证据，生成可追溯学习档案。
```

调用 Skill **不会**自动读取 Claude Code 历史会话。要把某次会话当作证据，必须先复制对应 JSONL，再用 `--authorize-claude-code-export` 对该文件单独授权。

## OpenCode

在待分析项目里启动 OpenCode，使用 `$learntrace`。Skill 会先检查 CLI，再跑一次 `learntrace discover`，只列出候选范围、不读文件。学生确认前不要读取这些材料。OpenCode 会话导出同样必须单独提供并授权。

CLI 只用本地确定性规则，不调用远程模型，也不读 `LEARNTRACE_*`。更细的语义理解由宿主模型承担。Skill 是工作流约束，不是操作系统沙箱；工具权限仍由宿主配置和用户审批决定。

## 本机网页（可选）

```powershell
learntrace ui <project-dir>
```

只监听本机回环地址。启动时写入一次性访问令牌（HttpOnly Cookie），只接受本机 Host。不扫描本机其它 Agent，也不要求安装 Pi CLI。

开始分析前在页面配置：API 地址、模型 ID、协议（Anthropic Messages / OpenAI Chat Completions / OpenAI Responses）、API Key、思考强度。API Key 进操作系统凭据库，不写进目标项目、浏览器存储或普通 JSON。

产物仍落在目标项目的 `.learntrace/`。隐藏推理和未授权会话正文不进 UI 数据库。同一项目同一时间只允许一项分析写入。

浏览器没自动打开时：

```powershell
learntrace ui <project-dir> --no-open
```

「测试连接」会发一次极小模型请求。若这一步就很慢，问题在 API 或网络，不是页面。

## 命令行

```powershell
learntrace discover <project-dir>
learntrace run <project-dir>
```

`discover` 只列候选路径。完整解析应在学生确认范围之后。Git 只读本地仓库，不访问远程。

常用回读：

```powershell
learntrace git-index <project-dir>
learntrace git-tree <project-dir> <revision>
learntrace git-evidence <project-dir> <commit-hash> --path src/example.py
learntrace git-file <project-dir> <revision> src/example.py --lines 120:180
learntrace git-worktree <project-dir>
```

`git-file` 每次最多 200 行或 1,000,000 字节。`.learntrace/` 和 `learning-record.md` 不作为用户开发证据。把 `.learntrace/` 加入目标项目的忽略规则，不要提交或直接分享。

### 产物

| 文件 | 用途 |
| --- | --- |
| `learning-record.md` | 给学生和老师看的阅读稿 |
| `.learntrace/archive-records.json` | 机器审计档案（来源、候选、告警、指纹） |
| `.learntrace/learning-questions.md` | 需要学生本人确认、补充或否认的问题 |

对外只分享检查过的阅读稿，不要上传机器档案或原始会话。

### 授权轨迹

未逐文件授权的导出不会被读取。LearnTrace 不会扫描 `~/.claude/projects/` 或 `~/.codex/sessions/`。

```powershell
learntrace run <project-dir> `
  --opencode-export <session.json> `
  --authorize-opencode-export <session.json> `
  --claude-code-export <session.jsonl> `
  --authorize-claude-code-export <session.jsonl> `
  --codex-export <rollout-session.jsonl> `
  --authorize-codex-export <rollout-session.jsonl>
```

单个 OpenCode 导出仍可用 `--authorized`。多个导出必须各自授权；结果按稳定 ID 去重，来源保留各自 session。JSONL：单文件 256 MiB、单行 16 MiB、单会话 2000 条事件、合并总量 10000 条；超出则保留最早事件并记截断警告。

### 学生确认

第二次运行复用首次快照，不再解析轨迹：

```powershell
learntrace run <project-dir> `
  --confirmations student-confirmations.json
```

每条记录需要 `candidate_id`、`decision` 和 RFC 3339 的 `confirmed_at`。`student_statement` 只能是学生原话；没有原话就省略，工具会记 `not_recorded`，不会代写。确认阶段不能同时再传文档、测试日志、Git 范围或会话导出。

`parse`、`adapt`、`archive` 可以分步跑。读会话时，`adapt` / `run` 必须带授权参数。已有结果文件时，`archive --trace-result` 必须指向本轮授权生成的文件；目录扫描不会把裸轨迹当成已授权输入。

## 证据怎么留

不必为了本工具改开发习惯。下面这些做法会让档案更好查：

- 在真实阶段边界提交，不要事后补造历史。
- 提交说明写清实际变化，避免只有「更新」。
- 保留任务书、设计和真实测试日志（含失败与再验证）。
- 需要呈现 AI 协作时，自行挑选会话并逐文件授权；不要把 API Key、私人聊天或原始导出提交到 Git。
- 团队项目先划清本人负责的范围。工具不按提交数量算贡献。

缺材料时会标「未记录」，不要为了报告好看补写不存在的经历。

## 布局

```text
src/learntrace/             Python 包与 CLI（含随包 UI）
src/learntrace/schemas/v0/  数据契约
skills/learntrace/          可安装 Skill
```

## 隐私

公开内容只用脱敏样例。密钥、个人信息、真实学生材料、完整 AI 对话不得提交。`local/` 始终被 Git 忽略。Markdown 里明确的 `/api`、`/v1` 一类接口路径会保留；其它以 `/` 开头、用途不清的文本可能按绝对路径脱敏。完整来源只在本机审计档案里。
