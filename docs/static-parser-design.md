# Task2 静态材料解析设计

## 目标与边界

Task2 将用户确认范围内的本地项目材料转换为 schema v0 `ObservableEvent`，供后续候选学习节点生成与人工确认使用。解析器只形成可直接定位的事实，不生成学习结论，也不推断作者、动机、个人贡献或 AI 会话与 Git 提交之间的对应关系。

解析过程不得导入或执行用户代码，不运行测试、Git hook、构建脚本或其他项目命令。所有公开夹具均为人工构造的脱敏材料。

## 两阶段流程

1. `discover_static_materials(root)` 遍历可见文件并返回候选文档、日志、项目清单和发现告警；此阶段不读取候选文件内容。
2. 宿主展示候选范围，由用户确认或删减。
3. `parse_static_materials(...)` 只解析确认路径，可选读取 Git 元数据。
4. `ParseResult.to_dict()` 在输出前逐条调用 schema v0 校验器，并检查整批事件 id 唯一性。
5. `write_parse_result(...)` 将通过校验的批量结果写入输出目录内随机、排他创建的临时文件，刷新后再以 UTF-8 JSON 原子替换目标路径；失败时清理临时文件。同一进程内指向同一规范化目标路径的调用会串行写出；Windows 跨进程或系统程序造成的短暂共享冲突会有限重试。

项目清单是解析报告的上下文，不伪装成 `ObservableEvent`。它记录文件树、常见源码、测试文件、文档、日志，以及任务书/报告/设计文档的确定性路径分类，供宿主展示分析范围。

## 批量 JSON

```json
{
  "parser_version": "v0",
  "analysis_scope": {
    "include_git": true,
    "documents": ["docs/report.md"],
    "test_logs": ["logs/pytest.log"]
  },
  "inventory": {
    "git_available": true,
    "files": ["docs/report.md"],
    "source_files": [],
    "test_files": [],
    "documents": ["docs/report.md"],
    "test_logs": ["logs/pytest.log"],
    "task_documents": [],
    "report_documents": ["docs/report.md"],
    "design_documents": [],
    "extension_counts": {".md": 1, ".log": 1}
  },
  "events": [],
  "warnings": []
}
```

所有路径均为项目相对 POSIX 路径。项目外路径只显示为 `[outside-project]/<文件名>`，避免在告警中泄漏本机绝对目录。输入中的等价路径按解析后的路径去重并保留首次出现顺序。

## 各来源规则

### Git

- Git 可执行文件只从 `PATH` 的绝对目录中解析为绝对路径，并排除位于待分析项目内的候选；后续子进程始终使用该绝对路径，避免 Windows 从当前项目目录执行伪造的 `git.exe`。
- `git rev-parse --show-toplevel` 返回的规范化仓库根目录必须与调用方确认的项目根目录一致。传入父仓库子目录时产生 `git_root_mismatch`，不读取父仓库提交或同级文件。
- 按拓扑顺序读取完整可达历史，默认不设置条数上限。调用方显式设置
  `max_commits` 时，它是侧支提交的逐条展开预算，不是按日期截断的硬窗口；
  first-parent 主线和所有 merge commit 始终保留。
  被省略的侧支提交生成聚合事实，`git_history_truncated` 同时记录保留、丢弃、
  主线、merge 和侧支数量。
- 每个提交产生一条总览事件，包含提交哈希、时间、提交信息和文件变更计数。
- `git-index` 为每条提交记录 `tree_id`、文件总数、顶层目录摘要和文件角色；同一
  `tree_id` 的布局可以复用。源码与测试同时出现在一个提交时只记录
  `same_commit`、`causal=false` 的导航关系，不推断测试由该源码变化导致。
- 每个新增、修改、删除、重命名或可识别的复制文件产生一条文件级事实，记录增删行数；二进制文件明确标记行数不可用。
- merge commit 的文件变化固定按“第一父提交到 merge commit”比较，避免按多个父提交重复生成文件事件。
- 默认使用 `-M -C` 做重命名和常规复制检测，不扫描所有未修改文件。调用方只有在确实需要更强复制检测时才设置 `find_copies_harder=True`；这会额外启用 `--find-copies-harder`，在大提交或 merge commit 上可能显著变慢。
- 重命名事件同时引用新旧路径；所有 Git 事实都引用完整提交哈希。Task2 只读取
  本地 Git，不解析、访问或依赖远程仓库地址。
- 需要进一步理解代码时，通过 `git-evidence` 按提交和可选文件路径导出完整 diff、
  改前版本和改后版本。导出是按需发生的，不把全部源码塞入 Task 2 事件；每项产物
  写入 `.learntrace/evidence/git/`，并在 `index.json` 标注来源、diff hunk、对应源码
  行范围、可用性和截断状态。本地授权证据保持原文，不与对外分享脱敏混为一层。
- `git-tree` 按需写出某一可达版本的完整 Git 文件树；`git-file` 每次最多按范围
  回读 200 行历史或 worktree 文本，并返回 revision、路径、行号、object ID 和
  截断状态；`git-worktree` 保存 staged、unstaged、untracked、删除、重命名、冲突
  及两类 diff，但不自动读取未跟踪文件内容，也不把 `.learntrace/` 和
  `learning-record.md` 等自身产物计入项目修改。
- 二进制、非 UTF-8、LFS 指针和 submodule 保留路径及 Git 元数据并明确不可用原因，
  不因一个异常文件终止其他证据。所有导航产物使用同目录临时文件和原子替换。
- 只调用不会执行 hook 的 Git 读取子命令，并为每个子命令设置 15 秒超时。提交元数据或任一 diff 子命令超时后，该提交不产生部分事实，而是记录 `git_commit_timeout`。

### Markdown/TXT

- 仅接受不超过 1 MiB 的 UTF-8 `.md` 和 `.txt`。
- Markdown 只在反引号/波浪线围栏代码块外识别标题，再以段落或完整代码块为不可拆事实块组合成约 600 字符的事件；代码中的 `#` 不会拆分章节。
- TXT 按段落生成事件。
- 不以省略号截断正文；较长章节拆成多条带准确行号的事件，从而保留全部可解析文本。

### 已有测试日志

- 支持 pytest 计数、耗时、`FAILED`/`ERROR` 用例，以及 Maven/JUnit 常见的 `Tests run` 汇总行和 Surefire 失败用例行。
- pytest 计数必须符合完整汇总行；用例行必须处于可识别的 pytest/JUnit 上下文。普通应用日志中的 `1 failed`、`2 errors` 或孤立的 `ERROR server.py` 不会被写成测试事实。
- 同一日志包含多轮汇总时全部保留，不只取最后一轮。
- pytest 用例路径同时按 POSIX 和 Windows 语法检查。只有确认位于项目根目录内的相对路径才生成文件 `SourceRef`；绝对路径、含 `..` 的越界路径、UNC/盘符路径及解析到项目外的符号链接都只保留测试日志来源。
- 非空但格式未知的日志保留一条“文件存在但格式未识别”的事实，并产生 `unsupported_test_log_format`，不把普通命令输出误写成测试结论。
- 解析器只读日志文件，从不调用 `pytest`、`npm test`、`mvn test` 等命令。

## 稳定性与失败策略

文档、日志和 Git 文件级事件 id 使用来源、位置与原始事实的 SHA-256 摘要确定性生成；提交总览 id 使用完整提交哈希。同一输入重复解析应得到完全一致的 JSON。整批输出发现重复 id 或不符合 schema v0 的事件时拒绝写出。JSON 写出使用同目录随机临时文件和原子替换，固定旧式 `.tmp` 路径即使已存在或为符号链接也不会被打开。同一进程内对同一规范化目标路径的并发调用会串行完成，每次调用均不会因同进程竞争而随机失败；不同目标路径仍可并行。跨进程写入不保证顺序，Windows 共享冲突只在有限次数内退避重试，重试耗尽或遇到其他真实权限/文件系统错误时仍向调用方抛出。成功写入的目标始终是某一次调用的完整结果，不会产生交错 JSON。

单个来源失败不会中止其他来源。常见告警包括：

- 路径/文件：`discovery_error`、`invalid_source`、`unsupported_document`、`file_too_large`、`invalid_utf8`、`read_error`、`empty_document`、`empty_test_log`；
- Git：`git_unavailable`、`not_git_repository`、`git_root_mismatch`、`git_no_commits`、`git_timeout`、`git_read_error`、`git_history_truncated`、`git_commit_timeout`、`git_commit_read_error`、`git_commit_format_error`；
- 日志格式：`unsupported_test_log_format`。

## M1 已知限制

Task 2 不做 AST/语义代码分析，不解析 PDF/Word，也不支持任意测试框架的完整
日志语法。未知日志只形成降级事实；代码语义由宿主 Agent按需读取原文后判断。
是否扩展新格式应先增加脱敏夹具和回归测试。

Maven Surefire 用例行依靠 `[ERROR] ... Time elapsed: ... <<< FAILURE!/ERROR!` 这一强格式特征独立识别，没有额外要求文件中同时出现汇总行。该规则当前误判风险很低；如果以后放宽 Maven 用例语法，应先增加文件级 Maven/JUnit 上下文判断。
