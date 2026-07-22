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
- Git 提交总览及新增、修改、删除、重命名、复制文件的文件级事实；
- 不静默截断的 UTF-8 Markdown 章节与 TXT 段落；
- pytest 与 Maven/JUnit 常见汇总、耗时和 `FAILED`/`ERROR` 用例；
- 输出前 schema v0 校验、全局事件 id 唯一性检查和本地 UTF-8 JSON 写出。

M1 不支持 AST、PDF、Word 或任意测试框架的完整日志语法，也不执行任何测试命令。

## 两阶段调用

材料发现只返回仓库相对路径，不读取文件内容。宿主应先向用户展示候选范围，取得确认后再解析：

```python
from pathlib import Path

from learntrace.parsers import (
    discover_static_materials,
    parse_static_materials,
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
)
write_parse_result(result, Path("local/static-result.json"))
```

`ParseResult.events` 是 `ObservableEvent` 元组；`ParseResult.warnings` 是未阻断整批解析的
结构化告警；`scope` 记录用户实际确认范围；`inventory` 是项目文件清单。
`to_dict()` 和 `write_parse_result()` 会逐条执行 schema v0 校验，并拒绝重复事件 id。

## 输出约定

- `git_commit`：每条提交有一条总览事件，并为每个文件变更生成事实事件；
- `document`：Markdown 按章节和完整段落/代码块分块，TXT 按段落生成事件；
- `test_log`：保留每轮结果汇总，并为可识别的 `FAILED`/`ERROR` 行生成事件；
- 文件来源始终使用仓库相对 POSIX 路径；
- 材料发现默认跳过隐藏目录、版本控制目录、虚拟环境、依赖和构建目录；
- 文档和日志来源附带行号，如 `docs/report.md:20-27`；
- 文档和日志 id 由来源路径、行号和内容的 SHA-256 摘要稳定生成；
- 解析失败会产生 `ParseWarning`，不会静默吞掉，也不会使其他文件停止解析。

详细规则、批量 JSON 结构、告警码和已知限制见
[静态材料解析设计](static-parser-design.md)。固定端到端场景位于
`tests/fixtures/static_parser/`，验收测试为 `tests/integration/test_static_parser_flow.py`。

## 维护约定

- 发现新格式时先增加脱敏夹具和测试，再扩展解析规则；
- 需要新增 `ObservableEvent` 字段或枚举时，先开 Issue 与数据契约负责人讨论；
- 不把提交信息或文档内容改写成作者、动机或学习结论；
- 不为验证日志而调用 `pytest`、`npm test`、`mvn test` 等命令。
