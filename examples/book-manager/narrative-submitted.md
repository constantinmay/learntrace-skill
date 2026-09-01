# 读书/资源管理系统课程项目 — 项目复盘

| 项目 | 日期 | 版本状态 | 证据可核查 |
|---|---|---|---|
| 读书/资源管理系统课程项目 | 2026-08-10 项目初始化至 2026-08-10 文档补全 | 已确认版,内容经本人复核 | 原始记录可应要求提供 |

> 本报告记录我在读书/资源管理系统课程项目中的开发过程、AI协作方式与学习收获。

## 项目概述

本项目是一个前后端分离的读书/资源管理系统，后端使用 Go 1.26 与 SQLite，前端使用 React 18 + TypeScript + Vite。README 记录了功能特性、目录结构、运行方式与接口说明。[^doc-08d307b][^doc-06e4fd3][^doc-056f7a4][^doc-1410e7d]

## 开发轨迹

| 阶段 | 关键结果 |
|---|---|
| 1 · 项目初始化 | 初始化 Git 仓库、添加 .gitignore 与 README 骨架 |
| 2 · 后端数据模型与 SQLite | 定义 books/categories 数据模型并完成 SQLite 初始化 |
| 3 · 后端 REST API | 新增 backend/handlers.go 并实现 REST API |
| 4 · 前端脚手架与组件开发 | 初始化 Vite + React + TS 脚手架，移除模板示例资源，并配置 /api 代理;实现前端类型定义、API 客户端、书籍列表/表单/详情/分类管理等组件 |
| 5 · 缺陷修复与文档补全 | 修复新建书籍 created_at 时间字段缺失默认值的问题 |

## 阶段详情

### 阶段 1 · 项目初始化

> 目标：初始化 Git 仓库、添加 .gitignore 与 README 骨架。

**Added**
- 初始化 Git 仓库、添加 .gitignore 与 README 骨架[^c-06f5aed]

本阶段关键结果证据：[^c-06f5aed]

<details><summary>原始提交(1 条)</summary>

- `06f5aed` 提交 06f5aed 的提交信息为“chore: 初始化项目仓库（.gitignore、README 骨架）”，记录 2 个文件变更（2 个新增）。
</details>


### 阶段 2 · 后端数据模型与 SQLite

> 目标：定义 books/categories 数据模型并完成 SQLite 初始化。

**Added**
- 定义 books/categories 数据模型并完成 SQLite 初始化[^c-193ebd4]

本阶段关键结果证据：[^c-193ebd4]

<details><summary>原始提交(1 条)</summary>

- `193ebd4` 提交 193ebd4 的提交信息为“feat(backend): 数据模型与 SQLite 初始化（books/categories 建表）”，记录 6 个文件变更（6 个新增）。
</details>


### 阶段 3 · 后端 REST API

> 目标：实现书籍/分类的 CRUD、搜索与分类筛选接口。

**Added**
- 新增 backend/handlers.go 并实现 REST API[^c-e345397]

本阶段关键结果证据：[^c-e345397]

<details><summary>原始提交(1 条)</summary>

- `e345397` 提交 e345397 的提交信息为“feat(backend): 实现书籍/分类 REST API（CRUD、搜索、分类筛选）”，记录 2 个文件变更（1 个新增、1 个修改）。
</details>


### 阶段 4 · 前端脚手架与组件开发

> 目标：搭建 Vite + React + TS 前端，实现书籍列表、表单、详情与分类管理。

**Added**
- 初始化 Vite + React + TS 脚手架，移除模板示例资源，并配置 /api 代理[^c-b61a8bf][^c-0e23a31]
- 实现前端类型定义、API 客户端、书籍列表/表单/详情/分类管理等组件[^c-45bb97e][^c-26af0db][^c-425f643][^c-33e8a11]

本阶段关键结果证据：[^c-b61a8bf][^c-0e23a31][^c-45bb97e][^c-26af0db][^c-425f643][^c-33e8a11]

<details><summary>原始提交(6 条)</summary>

- `b61a8bf` 提交 b61a8bf 的提交信息为“feat(frontend): 初始化 Vite + React + TS 脚手架并配置 /api 代理”，记录 19 个文件变更（19 个新增）。
- `0e23a31` 提交 0e23a31 的提交信息为“chore(frontend): 移除脚手架自带的模板示例资源”，记录 4 个文件变更（4 个删除）。
- `45bb97e` 提交 45bb97e 的提交信息为“feat(frontend): 类型定义与 API 客户端封装”，记录 2 个文件变更（2 个新增）。
- `26af0db` 提交 26af0db 的提交信息为“feat(frontend): 书籍列表页（表格、搜索、分类筛选）”，记录 4 个文件变更（1 个新增、3 个修改）。
- `425f643` 提交 425f643 的提交信息为“feat(frontend): 新增/编辑表单、详情弹窗与删除确认”，记录 6 个文件变更（3 个新增、3 个修改）。
- `33e8a11` 提交 33e8a11 的提交信息为“feat(frontend): 分类管理面板与整体样式打磨”，记录 3 个文件变更（1 个新增、2 个修改）。
</details>


### 阶段 5 · 缺陷修复与文档补全

> 目标：修复新建书籍 created_at 默认值问题，并完善 README。

**Fixed**
- 修复新建书籍 created_at 时间字段缺失默认值的问题[^c-3eac321]

本阶段关键结果证据：[^c-3eac321][^c-40601e5]

<details><summary>原始提交(2 条)</summary>

- `3eac321` 提交 3eac321 的提交信息为“fix(backend): 新建书籍时 created_at 使用与列表一致的 Go 时间”，记录 1 个文件变更（1 个修改）。
- `40601e5` 提交 40601e5 的提交信息为“docs: 完善项目 README（功能、运行说明、接口文档）”，记录 1 个文件变更（1 个修改）。
</details>

## 关键转折与我的处理

- **新建书籍时间字段修复**:学生在确认中说明，修复前曾手动复现新增书籍接口返回 500 的问题，原因是 created_at 字段缺少默认值；随后给 model.go 中的 CreatedAt 添加了 time.Now() 默认值，并再次调用接口验证返回 200 与正确时间戳。[^c-3eac321]

## 验证与质量

- 通过 learntrace verify-narrative 检查所有引用是否解析到 archive 中的 observable 事件。
- 未发现测试日志，因此未对测试通过性做结论。

## AI 使用声明

本项目在开发过程中使用了 OpenCode 作为辅助工具，我仅授权导出了最小保留级别的工具操作记录，所有学习结论与反思由本人确认或亲自撰写。

## 学习收获与下一步

1. 掌握了 Go 标准库 net/http 搭建 REST API 与 SQLite 数据持久化的基本流程。
2. 理解了前后端分离项目中 API 契约与 React 组件状态管理的重要性。
3. 认识到在新增字段时必须显式处理默认值，避免数据库行出现空值导致接口异常。

## 个人反思

通过本次项目，我体会到从仓库初始化到完整前后端联调需要持续迭代。最初我忽略了 created_at 默认值，直到手动测试新增接口才发现问题。这让我意识到写好单元测试和接口测试能够更早发现类似缺陷。

## 证据边界

- 未记录;当前档案未列出证据缺口。

---

[^doc-08d307b]: `README.md:1-7`(archive 事件 `evt-doc-08d307b1760355a1`)
[^doc-06e4fd3]: `README.md:9-17`(archive 事件 `evt-doc-06e4fd3544e92d3e`)
[^doc-056f7a4]: `README.md:19-24`(archive 事件 `evt-doc-056f7a4b42fe59b0`)
[^doc-1410e7d]: `README.md:100-102`(archive 事件 `evt-doc-1410e7d7f320bab9`)
[^c-06f5aed]: commit `06f5aed`(archive 事件 `evt-git-06f5aedf878836d2833895737a139c5b363d7f5a`)
[^c-193ebd4]: commit `193ebd4`(archive 事件 `evt-git-193ebd403a64a2827292e645de36d5f45392d403`)
[^c-e345397]: commit `e345397`(archive 事件 `evt-git-e34539734543d9f7a0612bd1a9db987ab71e2b69`)
[^c-b61a8bf]: commit `b61a8bf`(archive 事件 `evt-git-b61a8bf74e6959ab0af9e86439e5fa17d235fc08`)
[^c-0e23a31]: commit `0e23a31`(archive 事件 `evt-git-0e23a31f58682494d2bf50cd000def053a1b44b3`)
[^c-45bb97e]: commit `45bb97e`(archive 事件 `evt-git-45bb97e3215f3799c76d27fa1c3d89150a5b65ab`)
[^c-26af0db]: commit `26af0db`(archive 事件 `evt-git-26af0db283e5ec8985860b5a3923acc124c34561`)
[^c-425f643]: commit `425f643`(archive 事件 `evt-git-425f643c0b5ffaaf00f16cde3c5077f252760b1d`)
[^c-33e8a11]: commit `33e8a11`(archive 事件 `evt-git-33e8a1150ff01b5513344c19cf4130bc334ff6ba`)
[^c-3eac321]: commit `3eac321`(archive 事件 `evt-git-3eac32114182bb9bca32ce8f13527d8ed04e45ae`)
[^c-40601e5]: commit `40601e5`(archive 事件 `evt-git-40601e52098fdf155725f2fc5581844b2e4514ef`)
[^t-595e56e]: archive 事件 `evt-trace-595e56eee54cb4b4`
[^t-6cbecaa]: archive 事件 `evt-trace-6cbecaa0576a7cd4`
[^t-5888759]: archive 事件 `evt-trace-58887592b76aa9bf`
[^t-ed171df]: archive 事件 `evt-trace-ed171df96f4fa898`
[^t-b729286]: archive 事件 `evt-trace-b729286dc0a2751b`
[^t-7480430]: archive 事件 `evt-trace-74804304a5963ff2`
