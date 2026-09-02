# Examples

这里存放可公开、可复现且已经脱敏的演示项目与轨迹。示例不得依赖校内私有系统，也不得执行不受信任代码。

## 端到端报告示例（结构与呈现参考）

以下目录是 `skills/learntrace` 完整流程（run → 学生确认 → narrative
payload → verify/render）在真实项目上的一次走查产出，可用于对照
`narrative-payload.schema.json` 与 `skills/learntrace/references/narrative-payload.md`：

- `book-manager/`：单人 Go + React 项目；`narrative-payload.json` 为
  working 版输入，`narrative-rendered.md` 为其渲染结果，
  `narrative-submitted.json` / `narrative-submitted.md` 为 submitted 版。
- `province-economy/`：多人协作 Python 项目（含 merge commit 与 587 条
  消息的授权 OpenCode 导出），文件结构同上。

说明：

- 示例中的学生确认与 submitted 版反思/收获为**模拟填写**，仅用于演示
  呈现契约，不代表真实学生陈述。province-economy 为多人协作项目，其
  示例措辞已区分项目整体进展与个人确认的经历。
- payload 中的引用 ID 指向走查时生成的本地档案
  （`.learntrace/archive-records.json`），档案与原始会话导出不随示例发布；
  因此这些 payload 无法在仓库内独立通过 `verify-narrative`，仅作结构参考。
- 报告中的路径均为项目相对路径；AI 协作章节仅含最小保留字段（时间、
  工具类型、规范化相对路径、命令摘要、来源宿主），不含会话正文。
