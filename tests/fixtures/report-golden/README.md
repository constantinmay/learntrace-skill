# report-golden:报告呈现层金标工件

依据 `docs/learning-trajectory-design.md` §12,这些文件是渲染层重构的目标产物,
**先于实现固定**,用作后续回归对照。按项目分目录:

## book-manager/(线性历史、单作者、1 条被否认候选)

输入:12 条 Git 提交、13 条 README 文档事实、125 条授权 OpenCode 轨迹。

- `learning-record.md` — 理想未确认工作版;
- `learning-record.submitted.md` — 理想上交版(面向教师);
- `learning-questions.md` — 可选复盘提示(2 条);
- `student-answers.simulated.json` — **模拟**学生回答(非真实材料),驱动工作版到
  上交版的确认流程;
- `archive-stages.json` — 阶段层目标结构草案(v0 纯新增)。

## province-economy/(多分支 + PR 合并 + 历史截断 + 5 条已处理候选)

输入:52 条提交事件(Git 历史截断于最新 50 条)、283 条文档事实、400 条授权轨迹、
1 条未识别格式测试日志、3 条解析告警;候选 5 条(1 确认、2 补充、2 否认),LLM 模式。

- `learning-record.md` — 理想渲染版:阶段按主线合入点划分(first-parent),
  侧支提交折叠到 merge 点下,rebase 重放副本按内容指纹去重,截断历史作为
  「阶段 0」显式标注;补充/确认线索进正文,否认线索入附录;
- `learning-record.submitted.md` — 理想上交版:第一人称封面信,两条模拟回答
  融入叙事(早期实现补充、测试运行结果),被否认线索不含,「这是agent问题」
  的纠偏在 AI 使用声明中以第一人称澄清;
- `student-answers.simulated.json` — **模拟**学生回答(非真实材料),与档案中
  两条否认确认保持一致(agent.log 按调试日志处理);
- `learning-questions.md` — 可选复盘提示(2 条,证据缺口驱动);
- `archive-stages.json` — 阶段结构草案,含 merge_point_event_ids 与去重策略。
- 合并消息中的用户名已在正文中以 PR 号代称,事件 ID 保持可溯源。

## 使用方式

- 渲染重构完成后,以同一输入重跑生成,逐节对照本目录工件;
- 允许措辞差异,不允许结构回退(内部 ID 进正文、工具计数、缺失证据缺口标注、
  merge commit 作阶段标题、提交跨阶段重复、上交版残留未确认徽章等视为回归);
- 两版对照:上交版与工作版的事实层三节(项目概述/开发轨迹/阶段详情)必须
  逐字一致,差异只允许在叙事层;
- `archive-stages.json` 是目标结构草案,随实现稳定后并入 schema v1 讨论。
