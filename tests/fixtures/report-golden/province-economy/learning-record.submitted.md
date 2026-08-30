# 省域经济可视化项目 — 个人学习档案

| 项目 | 日期 | 版本状态 | 证据可核查 |
|---|---|---|---|
| 省域经济数据可视化（团队项目，本档案为本人工作部分） | 2026-05-02 → 2026-06-02 | 已确认版，内容经本人复核 | 原始记录可应要求提供 |

> 这是一个三人协作的课程项目。本档案只覆盖**我本人名下**的工作：模型分析模块、可视化联调修复、多年份数据接入、Docker 容器化与集成合并、文档收尾。可视化模块与 AI 助手模块的主体由队友实现，不在本档案范围内，下文仅在交接处标注协作边界。
> 本文档的原始记录由 LearnTrace 从我的 Git 提交、项目文档、测试日志和经授权的 AI 工具记录自动生成，全部内容经我本人逐条确认或修改。

## 项目概述

一个中国省域经济数据的可视化分析项目（团队项目）：覆盖 2019–2024 年分省年度数据，提供热力图、雷达图、趋势图与排名等可视化，后期加入 AI 智能助手模块，并完成 Docker 容器化部署[^doc-overview]。

本人在项目中的工作（按提交范围）：模型分析模块、可视化联调修复、多年份数据接入、Docker 容器化与集成合并、文档收尾。

## 开发轨迹

阶段按**主线合入点**划分；仅含本人名下提交，协作方提交不在档案内；早期历史因档案截断仅有部分记录（见证据边界）。

| 阶段 | 关键结果 |
|---|---|
| 0 · 初始化与模型分析模块（记录不完整） | 算法初步实现、年份选择与缓存机制（仅有合并记录，过程证据缺失） |
| 1 · 可视化联调（合入 PR #3) | 修复热力图兼容性、修复雷达图并添加年份切换；server 重构为独立包 |
| 2 · 多年份数据接入（合入 PR #4) | 主线提交完成多年份数据添加；PR 内容由协作方实现，本人完成合入 |
| 3 · Docker 容器化（合入 PR #5) | Docker 部署、使用文档与演示须知；Go 重构 |
| 4 · Agent 模块集成（合入 PR #6、#7) | 模块由协作方实现；本人完成其 docker 编排集成与 Excalidraw 绘图修复 |
| 5 · 收尾 | README 最终版；清理不必要文件 |

## 阶段详情

### 阶段 0 · 初始化与模型分析模块（记录不完整）

> ⚠️ 本阶段的提交过程**未完整记录**：档案的 Git 历史被截断（仅保留最新 50 条）,PR #2（模型分析模块）的合并点及本人侧支提交不在档案中。以下仅从合并消息推断工作范围，不作过程断言。

- 合并消息显示：算法初步实现、算法完成并在终端输出年度结果、添加年份选择与缓存机制并精简重构 main.py、完成 README 可视化对接；
- 同期协作方完成数据获取与清洗模块（PR #1)，不在本档案范围内。

### 阶段 1 · 可视化联调

> 目标：配合可视化模块（协作方实现）完成联调与修复。

**Fixed**
- 热力图兼容性及其余小 bug[^c-c02b2b5]。
- 雷达图修复，添加年份切换功能[^c-9908fe9]。

**Changed**
- server 提取为独立包 `src/server/`[^c-78e35bf]。
- README 更新[^c-9074914]；联调前建立存档点（提交信息"测试前")[^c-4e95d0a]。

<details><summary>原始提交（本人名下 5 条，随 PR #3 合入）</summary>

- `c02b2b5` 修复热力图和其余小bug
- `78e35bf` refactor: extract server to src/server/ package
- `9074914` README
- `9908fe9` 修复雷达图，添加年份切换功能
- `4e95d0a` 测试前
</details>

### 阶段 2 · 多年份数据接入

**Added**
- 多年份数据添加（主线直接提交）[^c-8a43204]。

**协作边界**
- 数据覆盖扩展至 2019–2024 年的 PR #4 由协作方实现，本人完成合入（合并点 `537af1d`)[^c-537af1d]。

### 阶段 3 · Docker 容器化

> 目标：容器化，支撑一键演示。

**Added**
- Docker 部署（容器化运行）[^c-21d6ae6]。
- Docker 使用说明与演示须知文档[^c-d2152e8]。

**Changed**
- Go 重构（服务实现调整）[^c-d85940f];README 更新[^c-40c3083]。

<details><summary>原始提交（随 PR #5 合入，档案内 5 条）</summary>

- `21d6ae6` Docker部署
- `02ec8c5` docs: add Docker usage instructions to README
- `d85940f` Go重构
- `40c3083` README更新
- `d2152e8` 添加演示须知

注：该分支同时携带本人及协作方早期提交的重放副本（rebase 产生），已按内容指纹去重；另有数条早期 Docker 提交（Dockerfile、compose 初始引入）不在档案截断窗口内，未列出。
</details>

### 阶段 4 · Agent 模块集成

> 目标：将协作方实现的 AI 智能助手模块纳入容器编排。

**Added**
- Agent 模块的 docker 编排（随 PR #7 合入）；容器内纳入中文嵌入模型缓存与 cloudflared[^c-a76c6e5]。

**Fixed**
- Excalidraw 绘图的一个小 bug（主线直接提交）[^c-a207f72]。

**协作边界**
- AI 助手模块本体（RAG + Tool Calling + SSE 流式、重构与优化、测试文件）由协作方实现（PR #6)，本人完成合入（合并点 `1f0aaa4`）与后续编排（PR #7，合并点 `7194e09`)[^c-merges4]。

<details><summary>原始提交（本人名下 2 条）</summary>

- `a76c6e5` 完成对agent模块的docker编排
- `a207f72` Excalidraw绘图加修一个小bug
</details>

### 阶段 5 · 收尾

**Changed**
- README 最终版；清理不必要文件[^c-cleanup]。

<details><summary>原始提交（3 条）</summary>

- `bfb85c6` README最终版
- `8a4b36c` 清理不必要文件
- `01f5e21` 继续清理
</details>

## 关键转折与我的处理

**早期实现的补充说明（本人陈述）**：阶段 0 的过程没有留下记录。算法是先在终端跑通的，能输出分省年度结果后才开始对接可视化；缓存机制是因为每次重算太慢才加的。那个阶段没有写自动化测试。

**图表修复的实际卡点是接口契约**。系统曾推断修复让我意识到可视化设计需要前瞻性；实际情况是 UI 交互未响应——前后端交接有 bug，我应该先检查好接口[^c-c02b2b5]。

**Docker 编排的约束调整是有意为之**。把中文嵌入模型缓存和 cloudflared 纳入编排，动机是为了演示时一键可用，这一点我确认[^c-a76c6e5]。

## 验证与质量

- 图表修复靠页面人工核对：切换年份和省份逐一点验渲染结果（本人陈述）;"测试前"提交[^c-4e95d0a]是联调前的存档点，其后没有留下测试记录。
- `logs/agent.log` 是调试日志，格式无法解析，不作为测试证据[^t-agentlog]。
- 早期算法阶段没有自动化测试，验证靠终端输出人工核对（本人陈述）；该过程无日志留存，除本人陈述外无其他佐证。

## AI 协作过程

本节是经本人复核的授权轨迹工作过程概览，不等同于学习结论，也不建立轨迹与 Git 提交的因果关系。项目早期的初始化、模型分析和可视化联调没有授权轨迹。

### 工作段 1：项目结构与任务拆解

- 主要活动：读取 -> 搜索 -> 计划项目资料。
- 覆盖范围：2026-05-19 至 2026-06-02 的授权会话。
- 文件焦点：`README`、`PLAN`、`pyproject`、`main.py`、`src/`。
- 依据：授权轨迹脚注[^t-segment-decomposition]。

### 工作段 2：Docker 容器化与演示准备

- 主要活动：读取 -> 编辑 -> 搜索编排计划。
- 覆盖范围：2026-05-19 至 2026-06-02 的授权会话。
- 文件焦点：Docker、compose、README、PLAN。
- 结果与限制：工具报错后的处理方式可观察，但具体判断未记录。
- 依据：授权轨迹脚注[^t-segment-container-demo]。

### 工作段 3：集成与验证

- 主要活动：读取 -> 编辑 -> 运行项目相关操作。
- 文件焦点：`src/`、Docker、`logs/agent.log`。
- 结果与限制：测试日志无法识别，不能据此判断验证结果。
- 依据：授权轨迹脚注[^t-segment-integration-check]。

以上工作段是确定性轨迹整理；完整命令参数、补丁正文、工具输出正文和原子工具次数不在本报告正文中。

## AI 使用声明

本项目开发过程中使用了 AI 编程助手（OpenCode）。我授权分析的轨迹覆盖 2026-05-19 至 2026-06-02；此前约两周的开发无授权轨迹。工作段只描述已授权会话中的可观察操作，不建立轨迹与 Git 提交的对应关系；项目事实和学习内容经本人复核。授权轨迹还记录了编排计划文件编辑报错和两次主动提问，内容未授权保留，不进入学习结论[^t-edit-fails][^t-question-b]。

## 学习收获与下一步

这次项目后我确认的三点：

1. **前后端接口契约要先检查**。图表修复的真正卡点是前后端交接的 bug，先验证接口再调样式可以少返工。
2. **演示约束会驱动技术决策**。"一键可用"的目标直接决定了把模型缓存打进镜像，这类约束应该提前列入设计。
3. **下一步：让验证留下记录**。图表修复靠人工核对、早期算法靠终端目验，都没有留档；以后我会把测试或核对过程留成可查的记录，让"验证过"有据可查。

---

[^doc-overview]: 项目文档含分省经济分析与工具接口说明，archive 事件 `evt-doc-07fa99e9322a94bd`、`evt-doc-000a694f2c435b05`。
[^t-segment-decomposition]: 代表性授权轨迹事件 `evt-trace-9cdd0094057575c0`、`evt-trace-76e0909519035e72`。
[^t-segment-container-demo]: 代表性授权轨迹事件 `evt-trace-9cdd0094057575c0`、`evt-trace-ef387c93fee4dc09`、`evt-trace-f236bf3db1d1a386`。
[^t-segment-integration-check]: 授权轨迹事件 `evt-trace-b20143bd0a3da858`、`evt-trace-164f0366491470ff`、`evt-test-26adddd592d1fd44`。
[^t-edit-fails]: 授权轨迹事件 `evt-trace-9cdd0094057575c0`、`evt-trace-ef387c93fee4dc09`、`evt-trace-f236bf3db1d1a386`、`evt-trace-b20143bd0a3da858`、`evt-trace-164f0366491470ff`。
[^t-question-b]: 授权轨迹事件 `evt-trace-76e0909519035e72`、`evt-trace-b1becb39ae829723`。
[^c-6708594e]: 合入锚点 `6708594e`（archive 事件 `evt-git-6708594e985b29365aaabae6179be8f67530dfb4`）。
[^c-c02b2b5]: commit `c02b2b5`(archive 事件 `evt-git-c02b2b5a6c5058a1a31524b5e71782a080fef41b`)
[^c-9908fe9]: commit `9908fe9`(archive 事件 `evt-git-9908fe98eb17374b3456bfa0d976e91106edd016`)
[^c-78e35bf]: commit `78e35bf`(archive 事件 `evt-git-78e35bf1bb13337179cb986395719f8f5b5844cf`)
[^c-9074914]: commit `9074914`(archive 事件 `evt-git-9074914f303e191f6bf0efc642804bf4f94cbc7c`)
[^c-4e95d0a]: commit `4e95d0a`(archive 事件 `evt-git-4e95d0a13e6989df4a30413a7a13e301ae41264f`)
[^c-8a43204]: commit `8a43204`(archive 事件 `evt-git-8a43204360eeb86844f132fe74a8ab5a1f97cf06`)
[^c-537af1d]: 合并点 `537af1d`(PR #4,archive 事件 `evt-git-537af1d6cafab130a92ba68b20156e6e5af5dee8`)
[^c-21d6ae6]: commit `21d6ae6`(archive 事件 `evt-git-21d6ae6d4d6303b6d3b38f2d80255232c6c8aa1f`)；合并点 `37da729`(PR #5）见 `evt-git-37da7299f8ef58946bc72aef6a34aac17267bf39`
[^c-d2152e8]: commit `d2152e8`(archive 事件 `evt-git-d2152e8556ff0f2cf9050eb92d0d27ddbf21d2e7`)、`evt-git-02ec8c5551ccd1dd3ef9868e322d0f36669f20ba`
[^c-d85940f]: commit `d85940f`(archive 事件 `evt-git-d85940f99489d5c768acf29b13f99f23ffbc494e`)
[^c-40c3083]: commit `40c3083`(archive 事件 `evt-git-40c3083270a7fe489594229c90004b5c2300d81d`)
[^c-a76c6e5]: commit `a76c6e5`(archive 事件 `evt-git-a76c6e52b158b518056e239b31a7ee993ebdba2f`)
[^c-a207f72]: commit `a207f72`(archive 事件 `evt-git-a207f72930106c084dd52a4b1af27382b14a82af`)
[^c-merges4]: 合并点 `1f0aaa4`(PR #6)archive 事件 `evt-git-1f0aaa42b0c52a9cd9275b46a06b2cb7ff66ffd6`；合并点 `7194e09`(PR #7)archive 事件 `evt-git-7194e092d85d18ff289127278ed2a07cc020359f`
[^c-cleanup]: archive 事件 `evt-git-bfb85c6805c051ab412b6b57872f185e9f44ba8f`、`evt-git-8a4b36c0d6c99fcb03781f5c40bce9722771a79b`、`evt-git-01f5e21f84c6b02e3e4155f0ece44dcd07b8e18e`
[^t-agentlog]: archive 事件 `evt-test-26adddd592d1fd44`
