# `src` 开发说明

`src/learntrace/` 存放 LearnTrace 的 Python 产品代码。开发功能时，通常需要同时修改对应模块中的 `.py` 文件和 `tests/` 下的测试，而不是只修改本说明文档。

## 模块分工

```text
src/learntrace/
├─ cli.py          命令行入口与参数编排
├─ models/         与 schema v0 对应的公共数据模型
├─ parsers/        Git、文件树、Markdown/TXT、既有日志解析
├─ adapters/       OpenCode 等 AI 编码宿主的轨迹适配
├─ privacy/        轨迹最小化、敏感信息过滤和脱敏
└─ reporting/      学习节点与 Markdown 学习档案生成
```

主要负责人：

- 数据契约任务线：`models/`
- 静态材料解析任务线：`parsers/`
- OpenCode 轨迹适配任务线：`adapters/` 和 `privacy/`
- Skill 与档案输出任务线：`cli.py` 和 `reporting/`

负责人表示主要维护和审核，不表示其他成员不能修改。跨模块接口变更需要相关负责人共同审核。

## 文件应该放在哪里

- 可复用的 Python 产品代码：`src/learntrace/`
- 单元测试：`tests/unit/` 下与源码模块对应的目录
- 跨模块流程测试：`tests/integration/`
- Schema 与字段约定：`schemas/v0/`
- Schema 和金标样例回归：`tests/contract/`
- 脱敏测试材料：`tests/fixtures/`
- Agent 使用的 Skill 指令：`skills/learntrace/`
- 项目设计和团队说明：`docs/`

不要把 Schema、测试样例或长篇设计文档放入 `src/learntrace/`。

## 开发一个功能

以增加 Git 元数据解析为例：

1. 从最新 `main` 创建 `feature/git-metadata-parser` 分支。
2. 在 `src/learntrace/parsers/` 增加或修改解析代码。
3. 在 `tests/unit/parsers/` 增加对应单元测试。
4. 如果输出格式发生变化，同步与数据契约负责人确认 `models/` 和 `schemas/v0/`。
5. 运行质量检查并通过 PR 合并。

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

## 代码边界

- 解析模块只读取数据，不执行被分析仓库中的代码、测试或日志命令。
- 公共数据通过 `models/` 定义，避免各模块自行创造不兼容的字典结构。
- 证据不足时输出明确的缺失状态，不补全或虚构事件。
- 默认只处理最小必要轨迹信息，完整聊天内容必须经过用户明确授权。
