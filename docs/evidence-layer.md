# 证据状态前端（可选，默认关闭）

## 一句话

给本地归档加一个**可选的、默认关闭的**前端，让 LearnTrace 不仅能「展示精简
档案」，还能「说明完整证据链在哪、断点在哪、某次快照是不是同一个」。

## 动机

当前闭环里，`learning-record.md` 是**投影**（给学生/老师看的精简片段），
`.learntrace/archive-records.json` 是**机器审计档案**（完整事实 + 指纹）。

机器归档已经有 `archive_manifest` + `content_fingerprint` + `--snapshot` 的状态
固化雏形。缺的是三样东西，把「审计档案」升级成「审计账本」：

1. **证据链的顺序是隐式的** —— 现有 4 种 `EventKind`（`git_commit` /
   `document` / `test_log` / `trace_record`）是干净的原始事实，但「谁先谁」靠下游推断器自己算，没有一个显式序号。

2. **快照没有绑定到日志位置和内容哈希的、清晰可复用的对象** —— 归档有内容指纹，但没有一个独立的、可比较的「检查点」对象。

3. **历史断档是隐式静默的** —— 现有 archiver 跳过无关 JSON、真实告警里也有一些证据缺失被写入 `pending_questions`，但「这里缺了什么」没有一个显式、可查询的间隔标记。

## 做了什么

**没有改动任何现有代码逻辑。** 新增了一个子包 `src/learntrace/evidence/`，并在
CLI 上加了 `--evidence-layer` 开关（默认**关**）。

三样“检查点”概念（一次就全写完的部分）来实现。它其实是我单独用了一个本地
证据模型的概念套组，这部分就是**复制**性质，不可能不写出来给队友看。我现在
把这些机制改写成 LearnTrace 自己的术语，并全部自实现（没有拷任何外部代码）。

具体分四个文件：

### 1. `chain.py` —— 语义事件日志

- 把 4 种原始 `EventKind` 归类为两个**语义层级**：`surface`（直接改动产物的
  提交/文档）vs `story`（发生时见证工作的测试日志/授权轨迹）。
- 每条日志项带显式 `seq` 序号 + 稳定内容哈希 `content_hash`。
- `LogGap` 表示链上的显式断点，只标「缺了什么」，不猜。

### 2. `checkpoint.py` —— 检查点

不可变状态快照：绑定了「取快照时的事件日志位置 `seq_after`」+ 内容哈希
`content_hash`。两个哈希不同的快照是**可证明**的不同状态。

### 3. `state.py` —— 状态 / 投影分离

`EvidenceState` 是完整审计状态（含候选、确认），`EvidenceGap` 是显式的缺失
标记——把「档案知道什么、知道它不知道什么」明确分开，而不是一个静默的洞。

### 4. `frontend.py` —— 从归档包派生

把现有管道的 `ArchiveBundle` 转成 `EvidenceState` + `Checkpoint`。检测三类
显式间隔：无测试运行记录、无项目目标记录、部分事件缺时间戳。

## 开关

- CLI：`learntrace run <dir> --evidence-layer` 或 `learntrace archive <dir>
  --evidence-layer`
- 编程接口：`write_learning_record_result(..., evidence_layer=True)`
- 默认**关**：不开开关时，输出与之前完全一致（旧归档 JSON + Markdown 投影
  不变，也不会多写任何文件）。
- 打开时：额外写入 `.learntrace/evidence-state.json`（检查点 + 状态 + 间隔
  标记），**不覆盖**旧产物。

## 测试

默认路径零破坏：全套 `333 passed, 3 skipped`，唯一失败仍是那条
`test_deeply_nested...` 的 pre-existing 失败（已由 PR #20 深度守卫覆盖，本改动
不重复处理）。

新增层：`build_evidence_state` 在合成事件集上可验证检查点哈希、`seq_after`、
间隔检测（缺时间戳 → `timestamps` 间隔）。

## 给队友看什么、怎么判断

请重点看三件事是否可以接受：

1. **「语义事件日志 / 检查点 / 状态投影分离 / 间隔标记」这四样是否值得进
   Task 4 主线**，还是先留在可选开关里继续看效果。
2. **开关默认关**的隔离方式是否符合团队「不破坏既有 4 人 schema v0 对齐」的
   前提（即：这一层完全加在现有三级证据之上，不要求任何人对齐新 schema）。
3. **间隔标记的语义**（`missing_source` / `missing_timestamps`）是否够用，
   要不要扩。

未决项（等队友反馈再定）：是否给 `evidence-state.json` 单独建 schema；是否把
间隔标记并入 `pending_questions`；是否给 `.learntrace` 增加这条新产物的
脱敏说明。
