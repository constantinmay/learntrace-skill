# Schema v0 数据契约交接文档

负责人：Liushenwuzhu-Alpaca（数据契约、金标样例与契约回归）
分支：`feat/schema-v0-contract`（未合并，待 PR）
状态：四项检查全绿（ruff check / ruff format --check / pyright / pytest）

## 1. 交付内容一览

| 交付物 | 位置 | 用途 |
| --- | --- | --- |
| 公共字段定义 | `schemas/v0/common.schema.json` | `schema_version`、`id`、`source_ref`、`missing_info` 等共享定义 |
| 事实 Schema | `schemas/v0/observable-event.schema.json` | 可观察事实记录 |
| 候选 Schema | `schemas/v0/learning-node-candidate.schema.json` | 系统候选学习节点 |
| 确认 Schema | `schemas/v0/student-confirmation.schema.json` | 学生对候选的回答 |
| 契约说明 | `schemas/v0/README.md` | 字段语义、证据等级、硬约束 |
| Python 模型 | `src/learntrace/models/records.py` | 三种记录 + `SourceRef` / `MissingInfo` 的 dataclass |
| 校验器 | `src/learntrace/models/validation.py` | `ContractValidator`，JSON 合规校验 |
| 金标样例 | `tests/fixtures/golden/` | 正常 4 例、证据不足 3 例、非法 4 例、九场景 31 例 |
| 预期结果清单 | `tests/fixtures/golden/expected-results.json` | 每个样例应通过还是被拒绝 |
| 契约回归测试 | `tests/contract/test_schema_v0.py` | 样例回归 + 跨记录规则 + 场景一致性 |

## 2. 三种记录与三条硬约束

- `observable_fact`：必须带 ≥1 条 `source_refs`（`git_commit` / `file` / `document` / `test_log` / `trace_record`）。
- `candidate_inference`：必须带 `node_type`（必填枚举：`follow_up` / `revise_ai_suggestion` / `fix_failed_approach` / `add_tests` / `adjust_constraints`，档案层按此筛选与统计，新增类型属于契约变更须开 Issue）、`basis_event_ids`（指向事实记录 id）和 `uncertainty`；`status` 只有 `proposed` / `resolved` 两种生命周期状态。`uncertainty` 必须以「高：」「中：」「低：」程度前缀开头，Schema `pattern` 强制，运行时校验同样生效。
- `student_confirmation`：独立记录，存 `candidate_id` + `decision`（`confirmed` / `supplemented` / `denied`）+ 学生原话。

硬约束（其他任务线必须遵守）：

1. 证据缺失时用结构化 `missing_info`：`{"status": "not_recorded", "note": "..."}`，展示为"未记录"；禁止用普通字符串表达缺失。
2. 候选的 `status=resolved` 仅表示存在对应的确认记录；学生的决定只写在确认记录里，禁止写进候选记录。
3. 没有授权轨迹就不产生 `trace_record` 事实，不得推断 AI 协作。

## 3. 各任务线怎么用

### 静态材料解析（成员 2）

- 产出 `ObservableEvent`，`kind` 取 `git_commit` / `document` / `test_log`，每条填真实 `source_refs`。
- 建议直接用模型构造再 `to_dict()`，而不是手写 dict：

```python
from learntrace.models import ObservableEvent, EventKind, SourceRef, SourceType

event = ObservableEvent(
    id="evt-0001",
    kind=EventKind.GIT_COMMIT,
    summary="……",
    source_refs=(SourceRef(type=SourceType.GIT_COMMIT, ref="<commit-hash>"),),
    occurred_at="2026-03-02T14:23:11+08:00",  # 可选，必须 RFC 3339
)
data = event.to_dict()  # 保证通过 schema
```

### 轨迹适配（成员 3）

首个适配器面向哪个 Agent（OpenCode / Claude Code / 其他）由成员 3 自行选择，契约层与宿主无关。

- 每条授权轨迹事件产出 `kind=trace_record` 的 `ObservableEvent`，`source_refs` 用 `type=trace_record` 指向轨迹记录 id。
- 默认最小化保留：时间、工具类型、文件路径、命令摘要、来源宿主；隐私过滤在你们模块内完成，契约层不存对话原文。
- 无轨迹时：不产出任何 `trace_record` 事件，并在下游用 `missing_info` 表达缺失。

#### 轨迹适配风险清单（来自一份真实 OpenCode Markdown 导出的分析）

以下是成员 1 分析真实导出后发现的问题，无论选择哪个 Agent 都建议对照自查：

1. **Secrets 会混在命令里**：真实轨迹中出现过把密码明文管道给特权命令的情况。命令摘要必须做 secrets 检测与脱敏，禁止原样复制命令全文。
2. **个人路径泄漏**：home 目录、外挂盘路径大量出现。本地档案可保留，导出分享版必须归一化。
3. **Markdown 导出格式脆弱**：`---` 分隔符与正文冲突、嵌套代码块、无逐条时间戳（只有会话级时间和单条耗时）。优先核实宿主是否有结构化内部存储（SQLite/JSON），Markdown 导出只作降级路径；存储布局随版本变化，实现前先验证，不要凭文档或记忆断言路径。
4. **系统注入噪音**：宿主插件会注入大段 `<system-reminder>` 和伪用户消息，必须用白名单提取——只从工具调用块提取五个最小字段，其余段落不进入提取范围。
5. **子代理黑箱**：`task` 类工具把活委托给子会话，父轨迹只有 prompt 和结果摘要，子代理具体操作不可见。只能记录“委托发生”，细节缺失写入 `uncertainty` 或解析报告。
6. **空消息与中断**：空调用、用户打断、工具执行中止都会出现。中断本身是有价值的学习信号，不应静默丢弃。
7. **失败策略分级**：单条记录不可解析 → 跳过并在解析报告中标记（条数、位置、原因）；整批格式版本不匹配 → 整体拒绝。禁止一条坏记录废掉整份轨迹，也禁止静默吞错。
8. **摘要策略分派**：bash 类输入常带 description 字段可直接作命令摘要；edit/task 类没有，退化为“工具名 + 目标路径”。契约层的硬性要求是：`observable_fact` 的每个字段都必须可直接追溯到轨迹原文，不得编造；是否引入大模型做脱水/摘要由成员 3 评估，但其产物只能标注为摘要或候选推断，不得充当原始事实记录。

### Skill 与学习档案（成员 4）

- 候选推断构造 `LearningNodeCandidate`，必填 `node_type`（`NodeType` 枚举）；学生作答后把 `status` 改为 `resolved` 并新增 `StudentConfirmation`。
- 学生未补充说明时，`student_statement` 传 `MissingInfo(note="...")`。
- 档案渲染遇到 `missing_info` 一律显示"未记录"。

### 运行时校验（所有任务线）

```python
from learntrace.models import ContractValidator

validator = ContractValidator()  # 源码布局自动定位 schemas/v0
validator.validate("observable_event", data)        # 不合规抛 ValidationError
errors = validator.iter_errors("learning_node_candidate", data)  # 拿全部错误
```

注意：Skill 打包或非源码布局场景必须显式传 `ContractValidator(schema_dir=...)`，不能依赖自动定位。

## 4. 金标样例用途

- 中段联调统一输入：成员 2/3 的解析器可用 `golden/normal/` 的两个事实样例对齐输出格式。
- 端到端演示：`normal/` 是"事实 → 候选 → 学生确认"完整闭环；`insufficient_evidence/` 演示无授权轨迹的降级行为。
- `scenarios/` 九场景样例（对应档案输出测试场景）：

| 目录 | 场景 |
| --- | --- |
| `01-revise-ai-suggestion-confirmed` | 修改 AI 建议，学生确认 |
| `02-revise-ai-suggestion-denied` | 修改 AI 建议候选被否认，陈述用 missing_info |
| `03-fix-failed-approach-supplemented` | 修复失败方案，学生补充说明 |
| `04-fix-failed-approach-missing-evidence` | 仅提交证据，uncertainty 注明无测试日志 |
| `05-add-tests-confirmed` | 补充测试，学生确认 |
| `06-follow-up-confirmed` | 多轮追问（两条 trace 事件），学生确认 |
| `07-adjust-constraints-supplemented` | 调整设计约束，学生补充 |
| `08-no-trace-degraded` | 无授权轨迹降级：仅事件，无候选无确认 |
| `09-sparse-evidence-high-uncertainty` | 证据稀疏：候选保持 proposed，uncertainty 以「高：」开头 |
- 所有样例均为人工构造、已脱敏，可直接提交公开仓库。

## 5. 修改契约的流程

`schemas/v0/` 四个 schema 文件（含共享的 `common.schema.json`）是四人冻结的公共契约，任何字段或枚举变更：

1. 先在对应 Issue 中记录变更内容并 @ 相关任务线负责人共同审核。
2. 同步修改 `records.py` 模型、受影响的金标样例和 `expected-results.json`。
3. 新增样例必须在 `expected-results.json` 登记，否则覆盖率检查直接失败。
4. 跑完整检查：ruff check、ruff format --check、pyright、pytest 全绿后才允许合并。

## 6. 维护要点

- 依赖：`jsonschema>=4.23,<5` + `rfc3339-validator>=0.1.4,<1`（后者是 `date-time` 格式校验的后端，缺了它非法日期会被静默放过），均已锁定 `uv.lock`。
- 校验器内部用 `referencing.Registry` 解析相对 `$ref` 并启用 `FormatChecker`；不要绕开它直接调 `jsonschema.validate`。
- 测试样例只用人工构造或脱敏材料，禁止提交真实学生材料或原始 AI 对话。

## 7. 已知边界（v0 有意不做）

- 模型暂不提供 `from_dict()`；如解析侧需要，开 Issue 讨论后补。
- 跨记录一致性（basis 引用存在、resolved 有确认）目前只在契约测试中检查，未提供运行时 API。
- `uv build` 产出的 wheel 不含 `schemas/v0/*.schema.json`：在非源码布局环境调用 `ContractValidator()`（无参）会因 `default_schema_dir()` 找不到目录而抛 `FileNotFoundError`。当前团队均在源码仓库布局开发不受影响；若日后发布正式 Python 包，需将 schema 作为 package data 打包并增加 wheel 安装后的校验测试。
- `student_confirmation` 不强制 `source_refs`，其来源视为与学生的对话本身。
- 来源宿主/Agent 独立字段已评估，v0/v0.1 均不增加：文件级归属已足够，会话内 Agent 身份暂无消费者；过渡期间来源宿主写入 `source_ref.note` 即可。
- Markdown 导出无稳定记录 id：适配器需自行合成（如 `session_id + 消息序号`），重导出后 id 会漂移，实现时注意幂等与去重。
