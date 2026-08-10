# 贡献指南

## 开始开发

1. 安装 Python 3.11 和 `uv`。
2. 运行 `uv sync --dev` 创建统一环境。
3. 从最新 `main` 创建与 Issue 对应的短分支。
4. 提交前运行 `uv run pre-commit run --all-files`。

## 分支命名

- `feature/<name>`：新增功能
- `fix/<name>`：缺陷修复
- `test/<name>`：测试与样例
- `docs/<name>`：文档
- `refactor/<name>`：不改变外部行为的重构

分支按任务命名，不按成员命名。一个 Issue 对应一个分支和一个 PR。

## 提交信息

使用 Conventional Commits，例如：

```text
feat(parser): add git metadata reader
fix(schema): preserve missing evidence state
test(contract): add insufficient-evidence fixture
docs: clarify privacy boundary
```

## 代码所有权

- 数据契约负责人：`schemas/`、`src/learntrace/models/`、`tests/contract/`、`tests/fixtures/`
- 静态解析负责人：`src/learntrace/parsers/` 及对应单元测试
- 轨迹适配负责人：`src/learntrace/adapters/`、`src/learntrace/privacy/` 及对应单元测试
- Skill 与输出负责人：`skills/`、`src/learntrace/reporting/`、CLI 及端到端测试

公共接口修改必须由受影响任务线至少一名成员审核。`pyproject.toml` 和 `uv.lock` 的依赖变更应在 PR 中单独说明。

## 合并要求

- 不直接推送 `main`
- 至少一名其他成员批准
- 自动检查全部通过
- 推荐使用 Squash merge
- 合并后删除功能分支

## 数据与隐私

不要提交密钥、账号、个人信息、真实学生材料或未经授权的 AI 轨迹。测试数据必须经过脱敏；证据不足时使用“未记录”，禁止补全或虚构事件。
