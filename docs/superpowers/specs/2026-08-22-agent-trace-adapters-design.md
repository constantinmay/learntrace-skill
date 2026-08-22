# Claude Code 与 Codex 轨迹适配器设计记录

> 状态：已实现（`feature/agent-trace-adapters` 分支）。
>
> 本记录是对 2026-07-27 OpenCode 适配器设计中"Claude Code or other Agent
> adapters"非目标边界的扩展，不改写该历史文档。

## 背景与决策

LearnTrace 原生只支持 OpenCode JSON 导出。本次新增 Claude Code 与 Codex 两个
JSONL 会话适配器，关键决策：

1. **显式授权输入模型**：适配器永不扫描 `~/.claude/projects/` 或
   `~/.codex/sessions/`；使用者复制会话文件后逐路径授权，与 OpenCode 的隐私
   边界一致。
2. **流式解析**（`adapters/_jsonl.py`）：真实 JSONL 会话普遍大于 OpenCode 的
   32 MiB 整读上限，改为逐行流式 + 子串级预过滤（不含 `tool_use` /
   `function_call` 标记的行不进 JSON 解析），内存只与产出事件数相关。文件上限
   放宽到 256 MiB，另设单行 16 MiB 上限与末尾截断行容忍。
3. **观察式状态判定**：事件在结果记录（`tool_result` / `function_call_output`）
   到达时才产出，状态来自观察（`is_error`、退出码）而非推测；未配对调用按
   "未完成"跳过并警告。结果正文永不进入产物。
4. **事件量限流**：单会话 2000 条、`run` 合并总量 10000 条（按时间保留最早），
   防止下游 reporting 的 O(T²) 跨事件配对和 LLM 全量 payload 被大规模会话击穿。
5. **opencode.py 零改动**：不提取共享基类；新适配器复制其已验证的不变量
   （授权先于 stat、白名单摘要、ContractValidator、按 ID 去重合并），共享代码
   收敛推迟到独立 follow-up。
6. **错误类型**：新增 `UnsupportedTraceFormatError(ValueError)`（纯增量），
   `UnsupportedOpenCodeFormatError` 原样保留。

## CLI

- `run` 新增对称旗标对：`--claude-code-export` / `--authorize-claude-code-export`、
  `--codex-export` / `--authorize-codex-export`；三宿主结果按稳定事件 ID 合并进
  同一 `task3-result.json`。
- 旧式 `--authorized` 保持 OpenCode 单导出专属；与新宿主导出同现时显式报错。
- `adapt` 新增 `--source {opencode,claude-code,codex}`（默认 opencode，行为不变）。

## 下游耦合（第一阶段处置）

- `render.py`：工具分类正则与审计附录前缀判断做加法式扩展，覆盖三宿主。
- `pipeline.py`：`_OPENCODE_SESSION_RE` 不匹配新前缀，新宿主保守地不参与跨会话
  候选（不崩溃）；扩展列为 follow-up（需同步修改候选文案与其测试断言）。

## 测试

- fixture 为人工合成数据（`FORBIDDEN_*` 泄漏标记），覆盖文件工具、命令、错误
  配对、未配对、坏行、孤儿结果。
- 单元测试镜像 `test_opencode.py` 模式：授权先于路径访问、逐路径授权、四种
  状态、上限与截断、ID 稳定性、泄漏断言、多会话合并去重。
- 集成测试覆盖 `write_trace_result` → `load_project_artifacts` 往返与 warning
  `location→source` 映射。
