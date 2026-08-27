# Task2 静态材料解析交接

负责人：constantinmay
Issue：[#2](https://github.com/constantinmay/learntrace-skill/issues/2)
分支：`feature/static-parser`

## 职责与安全边界

Task2 只读取调用方已确认范围内的本地静态材料，输出 schema v0
`ObservableEvent`。它不执行用户仓库代码或测试，不读取 AI 轨迹，不生成候选学习节点，
也不推断作者身份、学习动机或 AI 会话与 Git 提交关系。

M1 支持：

- 仓库文件清单、源码/测试文件和候选任务书、报告、设计文档分类；
- Git 提交总览及新增、修改、删除、重命名、可识别复制文件的文件级事实；
- merge commit 相对第一父提交的文件级事实，以及二进制文件的明确行数缺失状态；
- 不静默截断的 UTF-8 Markdown 章节与 TXT 段落；
- pytest 与 Maven/JUnit 常见汇总、耗时和 `FAILED`/`ERROR` 用例；
- 普通应用日志防误判，以及目录扫描失败时不泄漏绝对路径的结构化告警；
- Git 可执行文件绝对路径解析和仓库根目录严格校验，避免执行项目内伪造程序或读取父仓库同级文件；
- Markdown 围栏内标题防误判，以及 pytest 用例路径的跨平台项目边界校验；
- 输出前 schema v0 校验、全局事件 id 唯一性检查，以及基于随机临时文件、原子替换的本地 UTF-8 JSON 写出。

M1 不支持 AST、PDF、Word 或任意测试框架的完整日志语法，也不执行任何测试命令。

## 两阶段调用

材料发现只返回仓库相对路径，不读取文件内容。宿主应先向用户展示候选范围，取得确认后再解析：

```python
from pathlib import Path

from learntrace.parsers import (
    discover_static_materials,
    export_git_evidence,
    parse_static_materials,
    write_git_history_index,
    write_parse_result,
)

root = Path("/path/to/project")
materials = discover_static_materials(root)

# 宿主向用户展示 materials，由用户确认或删减路径后再调用。
result = parse_static_materials(
    root,
    document_paths=materials.documents,
    test_log_paths=materials.test_logs,
    include_git=materials.has_git,
    find_copies_harder=False,
)
write_parse_result(result, Path("local/static-result.json"))

# Task4 宿主 Agent 选中关键提交后，再按需回读原始代码证据。
history = write_git_history_index(root)
evidence = export_git_evidence(
    root,
    "<full-or-unambiguous-commit-hash>",
    paths=("src/example.py",),
)
print(history.output_path)
print(evidence.index_path)
```

`ParseResult.events` 是 `ObservableEvent` 元组；`ParseResult.warnings` 是未阻断整批解析的
结构化告警；`scope` 记录用户实际确认范围；`inventory` 是项目文件清单。
`to_dict()` 和 `write_parse_result()` 会逐条执行 schema v0 校验，并拒绝重复事件 id。

## 与 Task1、Task3、Task4 的对接

跨任务稳定接口是 Task1 定义的 `ObservableEvent`，不是 Task2 的批量结果类型：

- Task2 只产生 `git_commit`、`document`、`test_log` 静态事实；
- Task3 独立产生经授权的 `trace_record` 事实，不导入 Task2 的 `ParseResult`；
- Task4 合并两边的事件，重新检查整批 id 唯一性，再生成候选学习节点；
- `warnings`、`scope` 和 `inventory` 是解析报告，不是事实，不能作为学习结论；
- 没有日志时 Task2 不生成测试事实；没有授权轨迹时 Task3 不生成 `trace_record`；
- 轨迹与提交是否相关只能由 Task4 作为带不确定性的候选提出，不能由 Task2/Task3 写成事实。

Task4 或后续编排层可以按以下最小方式组合事件：

```python
from learntrace.models import ContractValidator

# authorized_trace_events 由 Task3 以同一 ObservableEvent 模型产生。
all_events = (*static_result.events, *authorized_trace_events)

event_ids = [event.id for event in all_events]
if len(event_ids) != len(set(event_ids)):
    raise ValueError("duplicate observable event ids across parsers")

validator = ContractValidator()
for event in all_events:
    validator.validate("observable_event", event.to_dict())
```

Task2 提供 Python API 和确定性的 CLI 证据入口，不负责学习语义判断或档案生成。
CLI/Skill 应先调用
`discover_static_materials()` 展示候选范围和发现告警，取得用户确认后再调用
`parse_static_materials()`，并把解析告警展示给用户，不能静默丢弃。

## 输出约定

- `git_commit`：每条提交有一条总览事件，并为每个文件变更生成事实事件；
- Git 历史默认完整读取。只有调用方显式设置 `max_commits` 时才应用侧支细节预算；
  first-parent 主线和 merge commit 不因预算被丢弃；
- Git 来源只包含本地 commit hash 和文件路径，不依赖远程仓库。Task4 宿主 Agent
  可调用 `export_git_evidence()` 或 `learntrace git-evidence` 回读指定提交的 diff 与
  前后源码，而无需在首次解析时把所有源码写入事件；
- `learntrace git-tree` 提供指定版本的完整目录；`learntrace git-file` 提供历史或
  worktree 的指定代码范围；`learntrace git-worktree` 提供未提交状态和 diff。
  三者都输出结构化本地定位信息，不生成远程链接；
- 历史索引包含 `tree_id`、文件数量、顶层布局、文件角色，以及仅用于导航的
  `same_commit`/`causal=false` 源码—测试关系；
- 二进制、非 UTF-8、LFS 和 submodule 保留路径、对象及不可用原因。截断证据可以
  通过 `git-file` 的相邻行范围继续读取，不能把缺失部分写成已验证结论；
- merge commit 固定相对第一父提交比较，避免多父提交产生重复文件事实；
- 默认使用轻量的 `-M -C`；只有调用方设置 `find_copies_harder=True` 时才扫描未修改文件寻找复制源。强复制检测可能让大型提交超时，普通分析不应默认开启；
- `document`：Markdown 按章节和完整段落/代码块分块，TXT 按段落生成事件；
- `test_log`：保留每轮结果汇总，并为可识别的 `FAILED`/`ERROR` 行生成事件；
- 普通日志中的零散计数或状态词不构成测试事实，无法确认框架时按未知格式降级；
- 文件来源始终使用仓库相对 POSIX 路径；
- 测试日志中的绝对路径、盘符/UNC 路径、`..` 越界路径和解析到项目外的符号链接不会生成文件来源，只保留日志行来源；
- 材料发现默认跳过隐藏目录、版本控制目录、虚拟环境、依赖和构建目录；
- 文档和日志来源附带行号，如 `docs/report.md:20-27`；
- 文档和日志 id 由来源路径、行号和内容的 SHA-256 摘要稳定生成；
- 解析失败会产生 `ParseWarning`，不会静默吞掉，也不会使其他文件停止解析。
- 材料发现本身遇到不可访问目录时产生 `discovery_error`，宿主应在范围确认前展示。
- `write_parse_result()` 不使用可预测的固定 `.tmp` 文件名；同一进程内对同一规范化目标路径的并发调用会串行完成，不同目标仍可并行。Windows 跨进程或系统程序造成的共享冲突会有限退避重试；重试耗尽及其他真实文件系统错误仍会抛出。每次成功调用只会原子提交一份完整结果，失败时清理本次随机临时文件。

详细规则、批量 JSON 结构、告警码和已知限制见
[静态材料解析设计](static-parser-design.md)。固定端到端场景位于
`tests/fixtures/static_parser/`，验收测试为 `tests/integration/test_static_parser_flow.py`。

### 真实项目复测

除仓库内的固定测试外，可使用公开项目
[`Liushenwuzhu-Alpaca/province-economy`](https://github.com/Liushenwuzhu-Alpaca/province-economy)
复测 Task2 的本地 Git 证据入口。该项目包含较长的提交历史和多个合并提交，适合
检查完整历史索引、first-parent 与 merge 保留、版本目录、提交 diff、前后源码以及
源码—测试的同提交导航。

将项目克隆到 LearnTrace 仓库外，或放入已被本地 Git 忽略的目录；不要把样例仓库、
它的 `.git/` 或运行生成的 `.learntrace/` 纳入 LearnTrace 的提交。LearnTrace 只读取
克隆后的本地 Git 数据，不依赖 GitHub API 或远程仓库链接。

```powershell
git clone https://github.com/Liushenwuzhu-Alpaca/province-economy.git
learntrace discover province-economy
learntrace git-index province-economy
learntrace git-tree province-economy HEAD
learntrace git-worktree province-economy
```

复测时应确认历史索引没有默认条数截断，合并提交仍可定位，并选择实际提交继续使用
`git-evidence` 和 `git-file` 回读 diff、文件前后版本及准确代码行。该仓库目前没有可由
Task2 识别的测试运行日志；结果应明确记录这项证据缺口，而不是推断测试已经运行或通过。

## 维护约定

- 发现新格式时先增加脱敏夹具和测试，再扩展解析规则；
- 需要新增 `ObservableEvent` 字段或枚举时，先开 Issue 与数据契约负责人讨论；
- 不把提交信息或文档内容改写成作者、动机或学习结论；
- 不为验证日志而调用 `pytest`、`npm test`、`mvn test` 等命令。

Task2 合并前除完整测试外，还应运行固定覆盖率门禁：

```powershell
uv run pytest tests/unit/parsers tests/integration/test_static_parser_flow.py `
  --cov=learntrace.parsers `
  --cov-report=term-missing `
  --cov-fail-under=90
```

该门禁只检查 Task2 专项套件，不放入普通 `pytest` 默认参数，避免开发者单独运行某个测试文件时被全模块覆盖率误伤。仓库建立统一 CI 后，应在 CI 中执行同一命令。
