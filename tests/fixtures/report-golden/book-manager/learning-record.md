# 项目复盘:读书/资源管理系统(未确认版)

| 项目 | 证据窗口 | 版本状态 | 复盘提示 | 证据缺口 |
|---|---|---|---|---|
| 读书/资源管理系统 | 2026-08-10 | 未确认版,未经本人复核 | 2 条可选(见 `.learntrace/learning-questions.md`) | 2 处 |

> 本文档由 LearnTrace 依据 Git 提交、项目文档和经授权的 OpenCode 轨迹生成。
> 标注「推断」的内容未经本人确认;「未记录」表示当前证据中不存在对应信息。
> 你可以修改、否认或删除任何部分;机器审计细节保存在 `.learntrace/archive-records.json`。

## 项目概述

一个前后端分离的读书/资源管理项目,用作课程大作业:前端 React 18 + TypeScript + Vite,后端 Go(标准库 `net/http`),数据库 SQLite(纯 Go 驱动,免 CGO)[^doc-intro]。记录的功能目标包括书籍增删改查、分类管理、关键字搜索、阅读状态与评分[^doc-features]。

## 开发轨迹

| 阶段 | 关键结果 |
|---|---|
| 1 · 初始化与数据层 | 仓库骨架;数据模型与 SQLite 建表;编译产物移出版本库 |
| 2 · 后端 REST API | 书籍/分类 CRUD、搜索与筛选 |
| 3 · 前端脚手架与数据接入 | Vite + React + TS 脚手架与 /api 代理;类型定义与 API 客户端 |
| 4 · 前端功能界面 | 书籍列表页;表单、详情与删除确认;分类管理与样式 |
| 5 · 文档收尾 | README 收尾 |

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

### 阶段 5 · 文档收尾

> 目标:完善项目文档。

**Changed**
- 完善 README:功能说明、运行说明、接口文档[^c-40601e5]。

<details><summary>原始提交(1 条)</summary>

- `40601e5` docs: 完善项目 README(功能、运行说明、接口文档)
</details>

## 关键转折

未记录;当前档案未产生经确认或补充的系统推断。

## AI 协作

- **可见性地图**：授权轨迹仅覆盖 **2026-08-10 当天一段约 40 分钟的集中会话**；项目其余开发期间无任何授权轨迹。本节图景仅代表该会话，不代表全程。
- **活动形状**：以命令行操作为主，辅以文件写入与编辑；会话中包含待办清单管理行为（计划驱动）。
- **文件焦点**：前端界面文件（`App.tsx`、`index.css`、`components/`）与 `backend/handlers.go`[^t-writes]；另有若干项目目录外路径。
- **关键插曲**:
  1. 会话开始处一次 AI 主动提问（question 工具，提问内容未授权保留）[^t-question];
  2. 一次 edit 以错误结束，路径在项目目录之外[^t-edit-err]——工具故障属系统噪声，不作为学习或能力信号；
  3. 调试期间通过命令行分别调用 curl、go、node 验证前后端行为[^t-debug]。
- **派生摘要**（经完整对话授权后生成，可否认）：*插曲·界面联调*——该会话以前端界面搭建为主线，边写边调；后端接口改动集中在书籍创建逻辑附近。<sub>派生 · 依据插曲内百余条原子记录</sub>
- **边界声明**：不建立轨迹操作与 Git 提交的对应关系；完整原子记录见机器审计档案。

## 验证与质量

- **未发现测试日志**;当前档案不能证明项目测试已运行或通过。
- 授权轨迹中有后端/前端的运行命令(`go`、`node`)与接口请求(`curl`)记录,但**未记录命令输出**,无法据此判断运行结果。
- README 记录了接口文档[^doc-api],与 handlers.go 的接口实现可人工对照,但档案中没有自动核对记录。

## 学习收获

- 未记录;本节内容需由本人确认或填写,系统不代写。
- 有 1 条系统线索已被本人否认,保留在附录备查。

## 个人反思

- 未记录;本节必须由本人填写。

## 证据边界

> ⚠️ **证据缺口 1**:未发现测试日志与运行输出 → 无法确认各功能是否经过验证及验证方式。
> ⚠️ **证据缺口 2**:调试窗口中观察到的具体问题未记录 → 无法确认调试期间针对的具体问题与验证方式。

## 附录

<details><summary>已否认线索(1 条,仅备查,不作负面评价)</summary>

- 线索:提交记录表明可能修复了一个实现问题(指向 `3eac321`)。
- 本人回应(2026-08-21):**否认**——「不知道,AI自己修的」。
- 处理:该线索不进入正文结论;相关事实保留在审计层。

</details>

<details><summary>审计摘要</summary>

- Schema:`v0`;候选生成模式:`stub`(本地确定性规则,未启用远程 LLM)。
- 事实分层:observable_fact 202 条 / candidate_inference 1 条 / student_confirmation 1 条。
- 档案指纹与完整来源索引见 `.learntrace/archive-records.json`。
- 解析告警:0 条。

</details>

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
[^c-40601e5]: commit `40601e5`(archive 事件 `evt-git-40601e52098fdf155725f2fc5581844b2e4514ef`)
[^t-question]: archive 事件 `evt-trace-e4ae6591a8636db6`
[^t-writes]: 例:archive 事件 `evt-trace-ec0526f401fc177f`、`evt-trace-b729286dc0a2751b`、`evt-trace-ac15850717458bff`;完整清单见 archive `events[]`。
[^t-edit-err]: archive 事件 `evt-trace-6a8153ce80b2ad9c`(edit 以错误结束,路径在项目目录之外)
[^t-debug]: 例:archive 事件 `evt-trace-1304434438911dd0`(curl)、`evt-trace-ed8a63dcc4a57ffb`(go)、`evt-trace-088825be1096dd27`(node)
