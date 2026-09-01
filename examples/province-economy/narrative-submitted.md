# province-economy 课程项目 — 项目复盘

| 项目 | 日期 | 版本状态 | 证据可核查 |
|---|---|---|---|
| province-economy 课程项目 | 2026-08-10 项目初始化至 2026-08-21 最终清理 | 已确认版,内容经本人复核 | 原始记录可应要求提供 |

> province-economy 是多人协作项目：本报告的项目概述与开发轨迹描述仓库整体进展（覆盖全部协作者的提交），AI 协作、确认记录与反思部分只代表我个人的经历，个人贡献范围以我确认的内容为准。

## 项目概述

本项目是一个面向中国省份经济数据的 Python 可视化分析系统，包含数据获取与清洗、模型分析、可视化、Web 服务、Docker 部署与 AI 问答助手等模块。README 与 data/README.md 记录了数据来源、目录结构与运行方式。[^doc-2dd4771][^doc-abae1ff]

## 开发轨迹

| 阶段 | 关键结果 |
|---|---|
| 1 · 项目初始化与数据获取 | 初始化项目仓库并添加数据获取、清洗模块及原始数据 |
| 2 · 数据分析与模型实现 | 实现算法初步模型与完成单年份分析;添加年份选择功能与缓存机制，精简 main.py |
| 3 · 数据合并与可视化 | 通过 Pull Request 合并 data 分支，扩展 2019-2024 年分省年度数据与指标表;添加可视化模块 api、choropleth、radar、ranking、trend 等 |
| 4 · 服务化与 Docker 部署 | 提取 server 到 src/server/ 包并修复雷达图、添加年份切换;添加多阶段 Dockerfile、docker-compose 与部署文档 |
| 5 · AI 助手模块 | 实现 AI 智能助手模块（RAG + Tool Calling + SSE 流式）;重构 Agent 模块迁移至 src/agent/ 并完成 Docker 编排 |
| 6 · 最终整理 | 更新 README 最终版并清理不必要文件 |

## 阶段详情

### 阶段 1 · 项目初始化与数据获取

> 目标：初始化仓库，添加数据获取、清洗模块及原始数据。

**Added**
- 初始化项目仓库并添加数据获取、清洗模块及原始数据[^c-05ad6ea][^c-46e915f]

本阶段关键结果证据：[^c-05ad6ea][^c-46e915f]

<details><summary>原始提交(2 条)</summary>

- `05ad6ea` 提交 05ad6ea 的提交信息为“初始化”，记录 11 个文件变更（11 个新增）。
- `46e915f` 提交 46e915f 的提交信息为“添加数据获取、清洗模块及原始数据”，记录 59 个文件变更（53 个新增、6 个修改）。
</details>


### 阶段 2 · 数据分析与模型实现

> 目标：实现算法分析模型并引入年份选择与缓存机制。

**Added**
- 实现算法初步模型与完成单年份分析[^c-c7967dc][^c-f859b82]

**Changed**
- 添加年份选择功能与缓存机制，精简 main.py[^c-3475a94]

本阶段关键结果证据：[^c-c7967dc][^c-f859b82]

<details><summary>原始提交(3 条)</summary>

- `c7967dc` 提交 c7967dc 的提交信息为“算法初步实现”，记录 3 个文件变更（2 个新增、1 个修改）。
- `f859b82` 提交 f859b82 的提交信息为“算法完成一年份内容并且终端输出文字结果”，记录 4 个文件变更（1 个新增、3 个修改）。
- `3475a94` 提交 3475a94 的提交信息为“添加选择年份功能和缓存机制，对main.py进行了精简化重构”，记录 4 个文件变更（1 个新增、2 个修改、1 个复制）。
</details>


### 阶段 3 · 数据合并与可视化

> 目标：合并数据分支，扩展数据覆盖范围，并添加可视化模块。

**Added**
- 通过 Pull Request 合并 data 分支，扩展 2019-2024 年分省年度数据与指标表[^c-537af1d][^c-94b1291]
- 添加可视化模块 api、choropleth、radar、ranking、trend 等[^c-5f3af1e][^c-3b6deda][^c-b20fb84][^c-cffc8a6][^c-a74cc46]

本阶段关键结果证据：[^c-537af1d]

<details><summary>原始提交(7 条)</summary>

- `537af1d` 提交 537af1d 的提交信息为“Merge pull request #4 from Liushenwuzhu-Alpaca/data”，记录 22 个文件变更（17 个新增、5 个修改）。
- `94b1291` 提交 94b1291 的提交信息为“扩展数据覆盖至2019-2024年，新增分省年度数据及指标表”，记录 22 个文件变更（17 个新增、5 个修改）。
- `5f3af1e` 提交 5f3af1e 的提交信息为“viz: add __init__.py”，记录 1 个文件变更（1 个新增）。
- `3b6deda` 提交 3b6deda 的提交信息为“viz: add api.py”，记录 1 个文件变更（1 个新增）。
- `b20fb84` 提交 b20fb84 的提交信息为“viz: add choropleth.py”，记录 1 个文件变更（1 个新增）。
- `cffc8a6` 提交 cffc8a6 的提交信息为“viz: add radar.py”，记录 1 个文件变更（1 个新增）。
- `a74cc46` 提交 a74cc46 的提交信息为“viz: add trend.py”，记录 1 个文件变更（1 个新增）。
</details>


### 阶段 4 · 服务化与 Docker 部署

> 目标：将分析能力封装为 Web 服务，并添加 Docker 编排支持。

**Added**
- 提取 server 到 src/server/ 包并修复雷达图、添加年份切换[^c-78e35bf][^c-9908fe9]
- 添加多阶段 Dockerfile、docker-compose 与部署文档[^c-6fa2a62][^c-c47046b][^c-21d6ae6]

本阶段关键结果证据：[^c-6fa2a62]

<details><summary>原始提交(5 条)</summary>

- `6fa2a62` 提交 6fa2a62 的提交信息为“feat(docker): add multi-stage Dockerfile and entrypoint”，记录 2 个文件变更（2 个新增）。
- `78e35bf` 提交 78e35bf 的提交信息为“refactor: extract server to src/server/ package”，记录 8 个文件变更（6 个新增、2 个修改）。
- `9908fe9` 提交 9908fe9 的提交信息为“修复雷达图，添加年份切换功能”，记录 12 个文件变更（7 个新增、5 个修改）。
- `c47046b` 提交 c47046b 的提交信息为“feat(docker): add docker-compose with 3-container architecture”，记录 1 个文件变更（1 个新增）。
- `21d6ae6` 提交 21d6ae6 的提交信息为“Docker部署”，记录 6 个文件变更（5 个修改、1 个删除）。
</details>


### 阶段 5 · AI 助手模块

> 目标：实现基于 RAG 与 Tool Calling 的 AI 智能助手，并接入 Docker 编排。

**Added**
- 实现 AI 智能助手模块（RAG + Tool Calling + SSE 流式）[^c-15e9eac]

**Changed**
- 重构 Agent 模块迁移至 src/agent/ 并完成 Docker 编排[^c-1088dff][^c-a76c6e5]

本阶段关键结果证据：[^c-15e9eac]

<details><summary>原始提交(3 条)</summary>

- `15e9eac` 提交 15e9eac 的提交信息为“实现 AI 智能助手模块（RAG + Tool Calling + SSE 流式）”，记录 20 个文件变更（12 个新增、8 个修改）。
- `1088dff` 提交 1088dff 的提交信息为“重构 Agent 模块：迁移至 src/agent/，升级中文 RAG 嵌入模型，添加生产级韧性与测试”，记录 25 个文件变更（7 个新增、6 个修改、11 个重命名、1 个复制）。
- `a76c6e5` 提交 a76c6e5 的提交信息为“完成对agent模块的docker编排”，记录 38 个文件变更（31 个新增、7 个修改）。
</details>


### 阶段 6 · 最终整理

> 目标：完成 README 最终版并清理不必要文件。

**Changed**
- 更新 README 最终版并清理不必要文件[^c-bfb85c6][^c-8a4b36c][^c-01f5e21]

本阶段关键结果证据：[^c-bfb85c6]

<details><summary>原始提交(3 条)</summary>

- `bfb85c6` 提交 bfb85c6 的提交信息为“README最终版”，记录 2 个文件变更（2 个修改）。
- `8a4b36c` 提交 8a4b36c 的提交信息为“清理不必要文件”，记录 17 个文件变更（17 个删除）。
- `01f5e21` 提交 01f5e21 的提交信息为“继续清理”，记录 24 个文件变更（24 个删除）。
</details>

## 关键转折与我的处理


## 验证与质量

- 通过 learntrace verify-narrative 检查所有引用是否解析到 archive 中的 observable 事件。
- 未发现项目自身测试日志，因此未对测试通过性做结论。

## AI 使用声明

本项目开发过程中使用了 OpenCode 作为辅助工具。我仅授权导出了最小保留级别的工具操作记录，所有学习结论与反思由本人确认或亲自撰写。

## 学习收获与下一步

1. 掌握了从国家统计局公开数据获取到清洗、分析的完整数据流水线设计。
2. 理解了将 Python 脚本分析升级为 Web 服务与 Docker 容器化部署的关键步骤。
3. 体会到在多人协作仓库中保持分支清晰、及时合并 PR 对项目推进的重要性。

## 个人反思

本次项目让我认识到，把数据分析脚本做成可复用的服务需要模块化和配置管理。最初 main.py 承担了所有职责，后来拆分为 data/、models/、visualization/、server/ 等包，并通过 Dockerfile 与 docker-compose.yml 实现了一键启动。最大的教训是数据缓存和模型下载路径需要显式管理，否则 Docker 镜像会过大或运行时报错。

## 证据边界

- 未记录;当前档案未列出证据缺口。

---

[^doc-2dd4771]: `README.md:176-181`(archive 事件 `evt-doc-2dd4771a065f3084`)
[^doc-abae1ff]: `data/README.md:1-5`(archive 事件 `evt-doc-abae1ff1babb4652`)
[^c-05ad6ea]: commit `05ad6ea`(archive 事件 `evt-git-05ad6ea5590cb476cfb0ed6928ab6e84c289080f`)
[^c-46e915f]: commit `46e915f`(archive 事件 `evt-git-46e915f3ce81ca7d577c865f57e7f8579cdd9b83`)
[^c-c7967dc]: commit `c7967dc`(archive 事件 `evt-git-c7967dc39471e816636b4710b0b34368a96d9014`)
[^c-f859b82]: commit `f859b82`(archive 事件 `evt-git-f859b8269f9db9f1ec41c8f68a7b4dc96d3d406e`)
[^c-3475a94]: commit `3475a94`(archive 事件 `evt-git-3475a94efaa9a1479ec4f382485e6ae4fde6bd49`)
[^c-537af1d]: commit `537af1d`(archive 事件 `evt-git-537af1d6cafab130a92ba68b20156e6e5af5dee8`)
[^c-94b1291]: commit `94b1291`(archive 事件 `evt-git-94b12911c3ad5d5b0ceee2c9c59b3a509bfb53e2`)
[^c-5f3af1e]: commit `5f3af1e`(archive 事件 `evt-git-5f3af1e6e7f35bc9e0eeb64c16fee6837c543e4b`)
[^c-3b6deda]: commit `3b6deda`(archive 事件 `evt-git-3b6deda18796b74a43dac5c02dd4e767963abaaf`)
[^c-b20fb84]: commit `b20fb84`(archive 事件 `evt-git-b20fb84c21879e95ed14ced7a495a0c9d8321cc3`)
[^c-cffc8a6]: commit `cffc8a6`(archive 事件 `evt-git-cffc8a6f136823c6705e4cda86c3ebbe72ed6412`)
[^c-a74cc46]: commit `a74cc46`(archive 事件 `evt-git-a74cc46b73717d635d0c884086365179899b655c`)
[^c-6fa2a62]: commit `6fa2a62`(archive 事件 `evt-git-6fa2a626925c3f277a4fca46f825ac8a03cbeeb1`)
[^c-78e35bf]: commit `78e35bf`(archive 事件 `evt-git-78e35bf1bb13337179cb986395719f8f5b5844cf`)
[^c-9908fe9]: commit `9908fe9`(archive 事件 `evt-git-9908fe98eb17374b3456bfa0d976e91106edd016`)
[^c-c47046b]: commit `c47046b`(archive 事件 `evt-git-c47046b50e56739f0f0ce1210146064e4ef5560e`)
[^c-21d6ae6]: commit `21d6ae6`(archive 事件 `evt-git-21d6ae6d4d6303b6d3b38f2d80255232c6c8aa1f`)
[^c-15e9eac]: commit `15e9eac`(archive 事件 `evt-git-15e9eac04bc038537f2da99135b0f013a6762e2a`)
[^c-1088dff]: commit `1088dff`(archive 事件 `evt-git-1088dff3147d75eb174714156ff9f556ada1ea84`)
[^c-a76c6e5]: commit `a76c6e5`(archive 事件 `evt-git-a76c6e52b158b518056e239b31a7ee993ebdba2f`)
[^c-bfb85c6]: commit `bfb85c6`(archive 事件 `evt-git-bfb85c6805c051ab412b6b57872f185e9f44ba8f`)
[^c-8a4b36c]: commit `8a4b36c`(archive 事件 `evt-git-8a4b36c0d6c99fcb03781f5c40bce9722771a79b`)
[^c-01f5e21]: commit `01f5e21`(archive 事件 `evt-git-01f5e21f84c6b02e3e4155f0ece44dcd07b8e18e`)
[^t-1fbf5fb]: archive 事件 `evt-trace-1fbf5fb7d228e598`
[^t-843dc56]: archive 事件 `evt-trace-843dc5685a988280`
[^t-35d5b7e]: archive 事件 `evt-trace-35d5b7e7e1fc8983`
[^t-71f6638]: archive 事件 `evt-trace-71f6638b1f3ea55b`
[^t-bf345b4]: archive 事件 `evt-trace-bf345b48c6359b84`
[^t-76de24a]: archive 事件 `evt-trace-76de24a7ffdad9fe`
[^t-6d2a2d2]: archive 事件 `evt-trace-6d2a2d2bd9d0bdad`
[^t-9dfd0e1]: archive 事件 `evt-trace-9dfd0e19291757b6`
[^t-1990d73]: archive 事件 `evt-trace-1990d7301bb27ae3`
[^t-c4a690a]: archive 事件 `evt-trace-c4a690a895e3d709`
[^t-557950c]: archive 事件 `evt-trace-557950c485b31337`
[^t-1f53ab0]: archive 事件 `evt-trace-1f53ab055201ca3e`
[^t-7eb2578]: archive 事件 `evt-trace-7eb25783959f1b1b`
