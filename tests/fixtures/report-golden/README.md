# report-golden: 报告呈现层金标工件

依据 `docs/learning-trajectory-design.md` §12，本目录固定报告呈现契约，供后续渲染实现回归对照。它不是提取层输出，也不实现 Issue #28 的 `message -> turn -> episode` 聚合。

## 三层职责

- `archive-stages.json`：Git 开发阶段的展示目标。它只描述提交、first-parent、合入锚点、历史截断和作者范围；不表示 AI trace episode，也不把轨迹操作与提交建立因果关系。
- `narrative-payload.golden.json`：宿主 Agent 到 CLI 的叙事输入目标。它验证报告字段、引用、确认状态、派生标记和证据边界；本 MVP 的工作段是确定性轨迹整理，不是宿主 Agent 自然语言摘要。
- `learning-record.md` 与 `learning-record.submitted.md`：最终报告的工作版和上交版展示目标。允许措辞调整，但不允许结构和隐私边界回退。

提取层的 v0 事实、候选和学生确认仍由 `tests/fixtures/golden/` 与 schema 契约验证。未来的轨迹聚合应使用独立合成夹具验证 `message -> turn -> episode` 的数据形状，而不是把本目录中的旧真实项目快照当作事件数量或阶段数量的硬编码基准。

## 报告呈现验收

工作版至少包含：项目概览、开发轨迹、AI 协作过程、学习线索、验证与质量、证据边界和机器审计附录。上交版保留事实性项目过程及已确认/已补充内容，不出现待确认徽章或被否认内容，不把自动整理写成学生已经学会的结论。

AI 协作正文使用“工作段”表达连续工作过程。每段至少说明：标题、日期级时间范围或覆盖范围、主要活动、文件焦点、命令类别、结果或限制和引用。正文不展示 `read`/`edit`/`bash` 工具次数、工具完成率、AI 与学生贡献比例、完整命令参数、补丁正文、工具输出正文，也不建立 trace 到 Git commit 的因果关系。

可直接观察的提问、工具错误等节点放在 `observed_touchpoints`；根据授权轨迹的工具、文件焦点、命令类别和时间/会话结构整理的连续过程放在 `work_segments`。自动工作段必须标记：

```json
{
  "derived": true,
  "derivation": "deterministic_trace_grouping",
  "deniable": true
}
```

`derived=true` 只表示派生摘要，不表示学习结论；“未记录”表示证据中没有对应信息。精确事件 ID 只出现在 JSON、脚注定义或机器审计区域，工作段引用必须能回到授权档案中的事件。

## book-manager/

线性历史、单作者的较小样例。它展示“了解结构”“界面与接口联调”“运行与检查”三个工作段，以及一个被否认候选如何留在工作版附录而不进入正文结论。

- `learning-record.md`：未确认工作版。
- `learning-record.submitted.md`：已确认上交版。
- `learning-questions.md`：证据缺口驱动的复盘提示。
- `student-answers.simulated.json`：模拟学生回答，非真实材料。
- `archive-stages.json`：Git 阶段结构草案，不是 trace episode 结构。
- `narrative-payload.golden.json`：工作版叙事 payload 目标。

## province-economy/

多分支、PR 合并、历史截断的多人协作样例。golden 使用 `author_scope: "self_only"` 表示只纳入本人名下提交和本人授权轨迹；协作方内容只保留协作边界说明，不做逐提交归因或贡献排名。`--author` 的解析行为由主线实现和对应测试负责，本目录只验证报告如何呈现结果。

- `learning-record.md`：仅含本人范围的未确认工作版。
- `learning-record.submitted.md`：仅含本人范围的已确认上交版。
- `learning-questions.md`：证据缺口驱动的复盘提示。
- `student-answers.simulated.json`：模拟学生回答，非真实材料。
- `archive-stages.json`：含作者范围、first-parent、合入锚点、去重和截断说明的 Git 阶段草案。
- `narrative-payload.golden.json`：保留作者范围和工作段边界的叙事 payload 目标。

## 使用方式

渲染重构完成后，以同一输入重跑并逐节对照本目录。允许自然语言措辞变化，不允许以下回归：内部 ID 进入正文、AI 协作退回工具计数、缺失证据未标注、合并提交被当作阶段内容、提交在多个阶段重复、上交版残留待确认或否认状态、或者自动整理被写成学生结论。

两版对照时，项目概览、开发轨迹、阶段详情三部分事实内容应逐字一致；差异只允许出现在确认、反思、AI 使用声明和附录范围。payload 中的引用必须能在对应报告的脚注或审计文本中找到；派生工作段必须同时标记 `derived`、`derivation` 和 `deniable`。

本目录中的旧事件 ID、事实计数、阶段数量和自然语言措辞来自展示快照，不是新提取层的精确验收结果。重新生成真实样例前，应先完成对应提取和聚合 issue，再整体校准这些展示背景。
