# 项目复盘:省域经济可视化与 AI 助手(未确认版)

| 项目 | 证据窗口 | 版本状态 | 复盘提示 | 证据缺口 |
|---|---|---|---|---|
| 省域经济数据可视化 + AI 智能助手 | 2026-05-02 → 2026-06-02 | 未确认版,未经本人复核 | 2 条可选(见 `.learntrace/learning-questions.md`) | 3 处 |

> 本文档由 LearnTrace 依据 Git 提交、项目文档、测试日志与经授权的 OpenCode 轨迹生成。
> 标注「推断」的内容未经本人确认;「未记录」表示当前证据中不存在对应信息。
> 你可以修改、否认或删除任何部分;机器审计细节保存在 `.learntrace/archive-records.json`。

## 项目概述

一个中国省域经济数据的可视化分析项目:覆盖 2019–2024 年分省年度数据,提供热力图、雷达图、趋势图与排名等可视化,后期加入基于 RAG + Tool Calling + SSE 流式的 AI 智能助手模块,并完成 Docker 容器化部署。项目文档包含分省份的经济分析笔记与工具接口说明[^doc-overview]。

## 开发轨迹

阶段按**主线合入点**划分;早期历史因档案截断仅有部分记录(见证据边界)。

| 阶段 | 关键结果 |
|---|---|
| 0 · 初始化、数据与算法模块(记录不完整) | 数据获取/清洗模块、算法初步实现与年份选择缓存(仅有合并记录,过程证据缺失) |
| 1 · 可视化模块(合入 PR #3) | 可视化包 7 个模块(热力图、雷达图、趋势、排名、样式、API);修复热力图与雷达图;server 重构 |
| 2 · 数据扩展(合入 PR #4) | 数据覆盖扩展至 2019–2024 年,新增分省年度数据与指标表 |
| 3 · Docker 部署(合入 PR #5) | Docker 部署与演示文档;Go 重构;携带早前工作的重放副本(已去重);部分早期容器化提交超出截断窗口 |
| 4 · AI 智能助手模块(合入 PR #6、#7) | RAG + Tool Calling + SSE 流式;迁移 src/agent/ 并新增 3 个测试文件;嵌入模型延迟加载等优化;完成 docker 编排 |
| 5 · 收尾 | README 最终版;清理不必要文件 |

## 阶段详情

### 阶段 0 · 初始化、数据与算法模块(记录不完整)

> ⚠️ 本阶段的提交过程**未完整记录**:档案的 Git 历史被截断(仅保留最新 50 条),PR #1(data)与 PR #2(models)的合并点及其侧支提交不在档案中。以下仅从文档与主线残留推断工作范围,不作过程断言。

- 存在数据获取与清洗模块、算法实现(含年份选择与缓存机制)的合并痕迹;
- 文档证据显示项目早期已有分省经济分析内容[^doc-overview]。

### 阶段 1 · 可视化模块

> 目标:建立可视化包结构,并修复图表缺陷。

**Added**
- 可视化包:`choropleth`(热力图)、`radar`(雷达图)、`trend`、`ranking`、`_style`、`api` 等 7 个模块[^c-viz-pkg]。

**Fixed**
- 热力图兼容性及其余小 bug[^c-c02b2b5]。
- 雷达图修复,添加年份切换功能[^c-9908fe9]。

**Changed**
- server 提取为独立包 `src/server/`[^c-78e35bf]。

<details><summary>原始提交(随 PR #3 合入,档案内 12 条)</summary>

- `f5d3d7d` viz: add __init__.py
- `e93fc5b` viz: add api.py
- `821ccdd` viz: add _style.py
- `426bc2b` viz: add choropleth.py
- `2273a9e` viz: add radar.py
- `db13e0b` viz: add ranking.py
- `a74cc46` viz: add trend.py
- `e37573d` viz: update main.py --viz flag
- `c02b2b5` 修复热力图和其余小bug
- `78e35bf` refactor: extract server to src/server/ package
- `9074914` README
- `9908fe9` 修复雷达图,添加年份切换功能
</details>

### 阶段 2 · 数据扩展

> 目标:扩展数据覆盖范围。

**Added**
- 数据覆盖扩展至 2019–2024 年,新增分省年度数据及指标表(随 PR #4 合入)[^c-data-ext]。

<details><summary>原始提交(2 条)</summary>

- `8a43204` 多年份数据添加(主线直接提交)
- `d08713a` 扩展数据覆盖至2019-2024年,新增分省年度数据及指标表(与 PR #4 侧支内容相同的重放副本,按内容指纹去重后计为同一变更)
</details>

### 阶段 3 · Docker 部署

> 目标:容器化,支撑一键演示。

**Added**
- Docker 部署(容器化运行)[^c-21d6ae6]。
- 演示须知与 Docker 使用说明文档[^c-d2152e8]。

**Changed**
- Go 重构(服务实现调整)[^c-d85940f]。

<details><summary>原始提交(随 PR #5 合入,档案内 4 条 + 重放副本已去重)</summary>

- `21d6ae6` Docker部署
- `02ec8c5` docs: add Docker usage instructions to README
- `d85940f` Go重构
- `d2152e8` 添加演示须知

注:该分支同时携带阶段 1–2 提交的重放副本(rebase 产生,内容指纹相同),已按内容去重,不计入本阶段工作量;另有部分早期 Docker 提交(如 Dockerfile 的初始引入)不在档案截断窗口内,未列出。
</details>

### 阶段 4 · AI 智能助手模块

> 目标:加入 AI 问答能力,并纳入容器编排。

**Added**
- AI 智能助手模块(RAG + Tool Calling + SSE 流式)[^c-15e9eac]。
- 测试文件 3 个:`test_agent_eval.py`、`test_tools.py`、`test_rag.py`(随重构提交)[^c-1088dff]。

**Changed**
- 重构 Agent 模块:迁移至 `src/agent/`,升级中文 RAG 嵌入模型[^c-1088dff]。
- 嵌入模型延迟加载;修复跨年份图表切换;会话改用 sessionStorage[^c-0e2ada4]。
- Agent 模块的 docker 编排(随 PR #7 合入);容器内纳入中文嵌入模型缓存与 cloudflared[^c-a76c6e5]。

**Fixed**
- Excalidraw 绘图的一个小 bug[^c-a207f72]。

<details><summary>原始提交(5 条)</summary>

- `15e9eac` 实现 AI 智能助手模块(RAG + Tool Calling + SSE 流式)
- `1088dff` 重构 Agent 模块:迁移至 src/agent/,升级中文 RAG 嵌入模型,添加生产级韧性与测试
- `0e2ada4` 优化 Agent 模块:嵌入模型延迟加载,修复跨年份图表切换,会话改用 sessionStorage
- `a76c6e5` 完成对agent模块的docker编排
- `a207f72` Excalidraw绘图加修一个小bug(主线直接提交)
</details>

### 阶段 5 · 收尾

**Changed**
- README 最终版;清理不必要文件[^c-cleanup]。

<details><summary>原始提交(3 条)</summary>

- `bfb85c6` README最终版
- `8a4b36c` 清理不必要文件
- `01f5e21` 继续清理
</details>

## 复盘事件

### 热力图与雷达图修复(已补充)

- 系统推断(中不确定性):通过修复图表 bug,可能意识到可视化模块对 GeoJSON 兼容性与年份切换需要提前设计。
- **本人补充纠正了方向**:实际遇到的核心问题是「UI交互未响应,前后端交接有bug,应该检查好接口」——即接口契约问题,而非可视化设计的前瞻性问题。
- 证据:修复提交 `c02b2b5`、`9908fe9`[^c-c02b2b5]。

### Docker 编排的约束调整(已确认)

- 系统推断(中不确定性):容器内无法联网下载中文嵌入模型,因此将 bge-small-zh-v1.5 模型缓存和 cloudflared 纳入编排。
- **本人确认**:动机是「为了演示时一键可用」。
- 证据:提交 `a76c6e5`[^c-a76c6e5]。

## AI 协作触点

本项目存在经授权的 OpenCode 轨迹,覆盖 Docker 化与 Agent 模块阶段。按产品约束,**不建立轨迹操作与 Git 提交的对应关系**:

| 位置 | 观察到的轨迹活动 | 说明 |
|---|---|---|
| Docker 容器化期间 | 多次 edit 报错,随后以 read/grep 定位文件内容后继续 | 系统曾推断这反映「先确认上下文再修改」的学习行为;**本人澄清:「这是agent问题」**——工具故障属系统噪声,不作为学习或能力信号 |
| 轨迹导出末尾 | 一条工具记录不完整,已跳过 | 解析告警,不影响其他记录 |

## 验证与质量

- 阶段 4 新增了 3 个测试文件(`test_agent_eval.py`、`test_tools.py`、`test_rag.py`),**测试代码存在**;但档案中**没有测试运行记录**,无法确认这些测试是否被执行及结果。
- 已有日志文件 `logs/agent.log`(204 行),格式未识别,未提取测试结果[^t-agentlog]。
- 早期阶段(初始化、数据与算法模块)的验证方式未记录。

## 学习收获

- 已确认的复盘事件见上文两节;**个人反思**部分未记录,需由本人填写,系统不代写。

## 证据边界

> ⚠️ **证据缺口 1**:Git 历史被截断(仅最新 50 条)→ PR #1/#2 时期的实现过程无法还原,阶段 0 仅有范围性描述。
> ⚠️ **证据缺口 2**:测试代码存在但无运行记录,`agent.log` 格式未识别 → 无法确认测试是否执行及结果。
> ⚠️ **证据缺口 3**:PR 评审讨论内容不在本地证据中 → 合入前的反馈与修改过程未记录。

## 附录

<details><summary>已被本人否认的线索(2 条,仅备查,不作负面评价)</summary>

- 线索:重构 Agent 模块时新增测试文件,可能意识到需要测试保障。**本人回应:否认——「忘了」**。
- 线索:`agent.log` 格式未识别可能触发改进测试输出或日志格式。**本人回应:否认——「不管」**。

</details>

<details><summary>审计摘要</summary>

- Schema:`v0`;候选生成模式:`llm`(远程 LLM 推断,输出经 schema 校验)。
- 事实分层:observable_fact 1329 条 / candidate_inference 5 条 / student_confirmation 5 条。
- 解析告警 3 条:Git 历史截断;`logs/agent.log` 格式不支持;1 条工具记录不完整已跳过。
- 档案指纹与完整来源索引见 `.learntrace/archive-records.json`。

</details>

---

[^doc-overview]: 项目文档含分省经济分析与工具接口说明,例:archive 事件 `evt-doc-07fa99e9322a94bd`、`evt-doc-000a694f2c435b05`;完整清单见 archive `events[]`。
[^c-viz-pkg]: archive 事件 `evt-git-f5d3d7df80677b32aa679296b63684fc49194559`、`evt-git-e93fc5b360f4a2b4ff56cb953fa5ad5d0d6214b8`、`evt-git-821ccdd1516cef50059483325850753bd2d7b606`、`evt-git-426bc2bd5918a5e16ccbd7a99cf8b7ef80478131`、`evt-git-2273a9e98741249f5a4b2ac48710905a6eeece26`、`evt-git-db13e0b5f4bc14fe543887fd3a8a2c5174061f3f`、`evt-git-a74cc46b73717d635d0c884086365179899b655c`;合并点 `6708594`(PR #3)见 `evt-git-6708594e985b29365aaabae6179be8f67530dfb4`
[^c-c02b2b5]: commit `c02b2b5`(archive 事件 `evt-git-c02b2b5a6c5058a1a31524b5e71782a080fef41b`)
[^c-9908fe9]: commit `9908fe9`(archive 事件 `evt-git-9908fe98eb17374b3456bfa0d976e91106edd016`)
[^c-78e35bf]: commit `78e35bf`(archive 事件 `evt-git-78e35bf1bb13337179cb986395719f8f5b5844cf`)
[^c-data-ext]: archive 事件 `evt-git-8a43204360eeb86844f132fe74a8ab5a1f97cf06`、`evt-git-d08713ad3a303b0d0249b5de5c65cd4a2a6df5ca`;合并点 `537af1d`(PR #4)见 `evt-git-537af1d6cafab130a92ba68b20156e6e5af5dee8`
[^c-21d6ae6]: commit `21d6ae6`(archive 事件 `evt-git-21d6ae6d4d6303b6d3b38f2d80255232c6c8aa1f`);合并点 `37da729`(PR #5)见 `evt-git-37da7299f8ef58946bc72aef6a34aac17267bf39`
[^c-d2152e8]: commit `d2152e8`(archive 事件 `evt-git-d2152e8556ff0f2cf9050eb92d0d27ddbf21d2e7`)、`evt-git-02ec8c5551ccd1dd3ef9868e322d0f36669f20ba`
[^c-d85940f]: commit `d85940f`(archive 事件 `evt-git-d85940f99489d5c768acf29b13f99f23ffbc494e`)
[^c-15e9eac]: commit `15e9eac`(archive 事件 `evt-git-15e9eac04bc038537f2da99135b0f013a6762e2a`);合并点 `1f0aaa4`(PR #6)见 `evt-git-1f0aaa42b0c52a9cd9275b46a06b2cb7ff66ffd6`
[^c-1088dff]: commit `1088dff`(archive 事件 `evt-git-1088dff3147d75eb174714156ff9f556ada1ea84`)
[^c-0e2ada4]: commit `0e2ada4`(archive 事件 `evt-git-0e2ada426787ee8452d1fb6d063b7a73cefcf3b6`)
[^c-a76c6e5]: commit `a76c6e5`(archive 事件 `evt-git-a76c6e52b158b518056e239b31a7ee993ebdba2f`);合并点 `7194e09`(PR #7)见 `evt-git-7194e092d85d18ff289127278ed2a07cc020359f`
[^c-a207f72]: commit `a207f72`(archive 事件 `evt-git-a207f72930106c084dd52a4b1af27382b14a82af`)
[^c-cleanup]: archive 事件 `evt-git-bfb85c6805c051ab412b6b57872f185e9f44ba8f`、`evt-git-8a4b36c0d6c99fcb03781f5c40bce9722771a79b`、`evt-git-01f5e21f84c6b02e3e4155f0ece44dcd07b8e18e`
[^t-agentlog]: archive 事件 `evt-test-26adddd592d1fd44`
