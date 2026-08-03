# Task 4 逻辑树

这份文件是一页版的 LearnTrace Task 4 逻辑树，目的是在提交 Task 4 的
PR 时，让评审不需要反推代码，也能快速看懂整条链路。

## 一句话概括

Task 4 会把本地项目证据整理成可审计的学习档案：先收集可观察事实，再
提出候选学习节点，把学生确认和系统推断分开保存，最后输出给人看和给程
序读的两种结果。

## 逻辑树

```text
Task 4：学习档案
|
+-- 1. 目标
|   |
|   +-- 从本地证据生成可追溯的学习档案
|   +-- 展示协作和学习过程，而不是做作者归因或作弊判断
|   +-- 严格分离事实、推断、学生确认
|
+-- 2. 输入
|   |
|   +-- 可观察事实
|   |   |
|   |   +-- git_commit
|   |   +-- document
|   |   +-- test_log
|   |   +-- trace_record
|   |
|   +-- 可选的学生确认记录
|   |
|   +-- 可选的解析告警
|   |
|   +-- 输入形态
|       |
|       +-- 单条 LearnTrace 记录 JSON
|       +-- 含 events / confirmations / warnings 的 JSON 容器
|       +-- 手工构造的 demo bundle
|
+-- 3. 加载层
|   |
|   +-- 遍历项目目录中的 JSON 文件
|   +-- 跳过 .git / .venv / node_modules 等噪音目录
|   +-- 把记录解析成类型化模型
|   +-- 按 schema 契约做校验
|   +-- 尽早拒绝格式错误或彼此冲突的证据
|
+-- 4. 推理层
|   |
|   +-- 选择 inferencer（reporting 正式负责学习节点推断）
|   |   |
|   |   +-- 未显式启用 LLM -> 走确定性 stub inferencer
|   |   |   |
|   |   |   +-- 无 LLM 环境变量
|   |   |   +-- 或有 key 但未设置 LEARNTRACE_LLM_ENABLED=1
|   |   +-- 显式启用（key + LEARNTRACE_LLM_ENABLED=1）-> 走 OpenAI 兼容 LLM inferencer
|   |   |
|   |   +-- 两路都必须通过同样证据分层与 schema 校验
|   |
|   +-- 产出候选学习节点
|   |   |
|   |   +-- follow_up
|   |   +-- revise_ai_suggestion
|   |   +-- fix_failed_approach
|   |   +-- add_tests
|   |   +-- adjust_constraints
|   |
|   +-- 每个候选必须带上
|       |
|       +-- node_type
|       +-- 候选陈述
|       +-- basis_event_ids
|       +-- uncertainty 不确定性说明
|       +-- question_to_student 给学生的确认问题
|
+-- 5. 信任边界
|   |
|   +-- 候选推断不能直接当成事实
|   +-- basis_event_ids 必须能指回真实 observable event
|   +-- 不合法的 LLM 输出直接丢弃，不能污染档案
|   +-- 学生决定必须与系统候选分开保存
|
+-- 6. 确认层
|   |
|   +-- 候选初始状态是 proposed
|   +-- StudentConfirmation 可以把候选标记为
|   |   |
|   |   +-- confirmed
|   |   +-- supplemented
|   |   +-- denied
|   |
|   +-- 只有存在确认记录，候选才会变成 resolved
|
+-- 7. 档案组装
|   |
|   +-- 去重 events
|   +-- 去重 confirmations
|   +-- 去重 warnings
|   +-- 生成稳定的 candidate id
|   +-- 对整个 bundle 再做一次整体验证
|
+-- 8. 输出
|   |
|   +-- Markdown 学习档案
|   |   |
|   |   +-- 项目概览
|   |   +-- 审计摘要
|   |   +-- 证据分层
|   |   +-- AI 使用情况
|   |   +-- 关键候选决策
|   |   +-- 支撑证据
|   |   +-- 反思
|   |   +-- 后续待确认问题
|   |
|   +-- 机器可读 archive JSON
|   |   |
|   |   +-- events
|   |   +-- candidates
|   |   +-- confirmations
|   |   +-- warnings
|   |   +-- pending_questions
|   |   +-- source_index
|   |   +-- candidate_links
|   |   +-- record_counts
|   |   +-- risk_flags
|   |   +-- quality_checks
|   |   +-- archive_manifest
|   |
|   +-- 待确认问题 Markdown
|
+-- 9. 可审计性
|   |
|   +-- 稳定的 SHA-256 内容指纹
|   +-- 分类型记录哈希
|   +-- 从 source ref 指到 event 和 candidate 的 provenance 索引
|   +-- 测试环境下可复现的确定性 stub 路径
|
+-- 10. 安全约束
    |
    +-- 不执行用户仓库代码
    +-- 不做作弊推断或作者归因
    +-- 不把系统猜测写成结论
    +-- 不编造缺失证据
    +-- API key 只允许从环境变量读取
    +-- LLM 出站载荷只含脱敏 summary（清邮箱/token/绝对路径）
    +--   source_refs、备注、仓库代码不发送
    +-- LLM 路径必须显式启用，缺省走本地 stub
```

## 评审检查清单

- 档案是不是从可观察事实出发，而不是直接下结论？
- 每个候选是不是都能追溯到明确的 `basis_event_ids`？
- 学生确认是不是和系统推断分开存放？
- 输出是不是同时包含给人看和给程序读的两种形式？
- 关闭 LLM 路径后，系统是不是仍然可审计、可复现？

## PR 说明

如果把这份文件放进 PR，评审可以把它当成下面几个实现区域的总地图：

- 输入加载与契约校验
- 候选推理
- 学生确认处理
- 档案序列化与审计元数据
- Markdown 报告与 demo 流程
