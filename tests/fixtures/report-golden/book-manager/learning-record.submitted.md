# 读书/资源管理系统 — 项目复盘

| 项目 | 日期 | 版本状态 | 证据可核查 |
|---|---|---|---|
| 读书/资源管理系统 | 2026-08-10 | 已确认版,内容经本人复核 | 原始记录可应要求提供 |

> 这是我课程大作业的过程复盘。项目由我个人完成;本文档的原始记录由
> LearnTrace 从我的 Git 提交、项目文档和经授权的 AI 工具记录自动生成,
> 全部内容经我本人逐条确认或修改。

## 项目概述

一个前后端分离的读书/资源管理项目,用作课程大作业:前端 React 18 + TypeScript + Vite,后端 Go(标准库 `net/http`),数据库 SQLite(纯 Go 驱动,免 CGO)[^doc-intro]。记录的功能目标包括书籍增删改查、分类管理、关键字搜索、阅读状态与评分[^doc-features]。

## 开发轨迹

| 阶段 | 关键结果 |
|---|---|
| 1 · 初始化与数据层 | 仓库骨架;数据模型与 SQLite 建表;编译产物移出版本库 |
| 2 · 后端 REST API | 书籍/分类 CRUD、搜索与筛选 |
| 3 · 前端脚手架与数据接入 | Vite + React + TS 脚手架与 /api 代理;类型定义与 API 客户端 |
| 4 · 前端功能界面 | 书籍列表页;表单、详情与删除确认;分类管理与样式 |
| 5 · 修复与文档收尾 | 统一 created_at 时间格式;完善 README |

## 阶段详情

### 阶段 1 · 初始化与数据层

> 目标:建立仓库骨架,并落定为后端服务的数据模型与存储。

**Added**
- 仓库骨架:`.gitignore` 与 README 雏形[^c-06f5aed]。
- 后端数据层:`backend/model.go`、`db.go` 等 6 个文件,books/categories 建表[^c-193ebd4]。

**Removed**
- 后端编译产物从版本库移出并加入 `.gitignore`[^c-3f0c8b4]。

<details><summary>原始提交(3 条)</summary>

- `06f5aed` chore: 初始化项目仓库(.gitignore、README 骨架)
- `193ebd4` feat(backend): 数据模型与 SQLite 初始化(books/categories 建表)
- `3f0c8b4` chore: 将后端编译产物加入 .gitignore 并从版本库移除
</details>

### 阶段 2 · 后端 REST API

> 目标:让书籍与分类数据可通过统一接口访问。

**Added**
- `backend/handlers.go`:书籍/分类 CRUD、关键字搜索、分类筛选(296 行)[^c-e345397]。

<details><summary>原始提交(1 条)</summary>

- `e345397` feat(backend): 实现书籍/分类 REST API(CRUD、搜索、分类筛选)
</details>

### 阶段 3 · 前端脚手架与数据接入

> 目标:建立前端工程结构,并打通到后端的访问通道。

**Added**
- Vite + React + TypeScript 脚手架,`/api` 代理到后端[^c-b61a8bf]。
- 类型定义与 API 客户端封装(`frontend/src/types.ts`、`api.ts`)[^c-45bb97e]。

**Removed**
- 脚手架自带的模板示例资源[^c-0e23a31]。

<details><summary>原始提交(3 条)</summary>

- `b61a8bf` feat(frontend): 初始化 Vite + React + TS 脚手架并配置 /api 代理
- `0e23a31` chore(frontend): 移除脚手架自带的模板示例资源
- `45bb97e` feat(frontend): 类型定义与 API 客户端封装
</details>

### 阶段 4 · 前端功能界面

> 目标:覆盖记录的功能目标——列表、搜索、表单、分类管理。

**Added**
- 书籍列表页(表格、搜索、分类筛选)[^c-26af0db]。
- 新增/编辑表单、详情弹窗与删除确认[^c-425f643]。
- 分类管理面板与整体样式[^c-33e8a11]。

<details><summary>原始提交(3 条)</summary>

- `26af0db` feat(frontend): 书籍列表页(表格、搜索、分类筛选)
- `425f643` feat(frontend): 新增/编辑表单、详情弹窗与删除确认
- `33e8a11` feat(frontend): 分类管理面板与整体样式打磨
</details>

### 阶段 5 · 修复与文档收尾

> 目标:修正接口行为差异,补齐项目文档。

**Fixed**
- 新建书籍接口的 `created_at` 与列表接口的时间格式不一致,统一为一致的 Go 时间格式[^c-3eac321]。

**Changed**
- 完善 README:功能说明、运行说明、接口文档[^c-40601e5]。

<details><summary>原始提交(2 条)</summary>

- `3eac321` fix(backend): 新建书籍时 created_at 使用与列表一致的 Go 时间
- `40601e5` docs: 完善项目 README(功能、运行说明、接口文档)
</details>

## 关键转折与我的处理

```mermaid
flowchart LR
    A["阶段 4 完成:前端功能界面"] --> B["运行与接口检查"]
    B --> C["修复 created_at 格式(commit 3eac321)"]
    C --> D["我用 curl 复核两个接口,格式已一致"]
```

功能界面完成后、文档收尾前没有新提交,这段时间记录了多次后端/前端的运行与接口检查[^t-debug]。`created_at` 格式不一致是 AI 助手在这个过程中发现并修复的,我当时只看到修复提交,不清楚问题的最初表现;事后我用 `curl` 分别请求了新建和列表接口,确认两处格式已经一致。

## 验证与质量

- 项目**没有自动化测试**;当时我本地同时启动后端和前端,手动把书籍的增删改查、搜索和分类筛选都操作了一遍,确认页面和接口返回正常(本人陈述)。
- 手动验证过程没有日志留存,除本人陈述外无其他佐证;这是本档案最主要的证据边界。
- README 记录了接口文档[^doc-api],与 `handlers.go` 的接口实现可人工对照。

## AI 使用声明

本项目开发过程中使用了 AI 编程助手(OpenCode)。我授权分析的轨迹为 2026-08-10 一段约 40 分钟的集中会话:AI 参与了前端界面搭建与后端接口联调,期间一次提问交互与一次工具报错已在工作版中列明;所有代码与文档均经我本人审阅、运行和修改。`created_at` 格式问题由 AI 定位并修复,修复结果经我本人手动验证。本报告的原始记录由 LearnTrace 从 Git 历史、项目文档与授权轨迹自动生成,插曲摘要经我本人确认,全部内容经我本人复核。

授权轨迹还记录了:开发开始前一次 AI 向我发起的提问(内容未授权保留)[^t-question];调试期间一次对项目外路径的 edit 工具报错[^t-edit-err],与项目内容无关。轨迹与提交之间的对应关系未做建立。

## 学习收获与下一步

这次项目后我确认的三点:

1. **接口字段格式要在写前端之前约定**。`created_at` 的不一致发生在两个后端接口之间,如果先约定响应格式再实现,这个问题不会出现。
2. **AI 修复的代码我也要能看懂并验证**。这次问题不是我定位的,但我用 `curl` 独立复核了修复结果,而不是默认它正确。
3. **手动验证不够用**。下一步我计划把这次的 curl 检查固化成脚本,为后端补上自动化测试,让验证过程留下可复查的记录。

---

[^doc-intro]: `README.md:1-7`(archive 事件 `evt-doc-08d307b1760355a1`)
[^doc-features]: `README.md:9-17`(archive 事件 `evt-doc-06e4fd3544e92d3e`)
[^doc-api]: `README.md:51-78`(archive 事件 `evt-doc-ef70b1b414f505e3`)
[^c-06f5aed]: commit `06f5aed`(archive 事件 `evt-git-06f5aedf878836d2833895737a139c5b363d7f5a`)
[^c-193ebd4]: commit `193ebd4`(archive 事件 `evt-git-193ebd403a64a2827292e645de36d5f45392d403`)
[^c-3f0c8b4]: commit `3f0c8b4`(archive 事件 `evt-git-3f0c8b4c3da0836ae0206feeb1e774c5fe4cc26a`)
[^c-e345397]: commit `e345397`(archive 事件 `evt-git-e34539734543d9f7a0612bd1a9db987ab71e2b69`)
[^c-b61a8bf]: commit `b61a8bf`(archive 事件 `evt-git-b61a8bf74e6959ab0af9e86439e5fa17d235fc08`)
[^c-0e23a31]: commit `0e23a31`(archive 事件 `evt-git-0e23a31f58682494d2bf50cd000def053a1b44b3`)
[^c-45bb97e]: commit `45bb97e`(archive 事件 `evt-git-45bb97e3215f3799c76d27fa1c3d89150a5b65ab`)
[^c-26af0db]: commit `26af0db`(archive 事件 `evt-git-26af0db283e5ec8985860b5a3923acc124c34561`)
[^c-425f643]: commit `425f643`(archive 事件 `evt-git-425f643c0b5ffaaf00f16cde3c5077f252760b1d`)
[^c-33e8a11]: commit `33e8a11`(archive 事件 `evt-git-33e8a1150ff01b5513344c19cf4130bc334ff6ba`)
[^c-3eac321]: commit `3eac321`(archive 事件 `evt-git-3eac32114182bb9bca32ce8f13527d8ed04e45ae`)
[^c-40601e5]: commit `40601e5`(archive 事件 `evt-git-40601e52098fdf155725f2fc5581844b2e4514ef`)
[^t-question]: archive 事件 `evt-trace-e4ae6591a8636db6`
[^t-edit-err]: archive 事件 `evt-trace-6a8153ce80b2ad9c`(edit 以错误结束,路径在项目目录之外)
[^t-debug]: 例:archive 事件 `evt-trace-1304434438911dd0`(curl)、`evt-trace-ed8a63dcc4a57ffb`(go)、`evt-trace-088825be1096dd27`(node)
