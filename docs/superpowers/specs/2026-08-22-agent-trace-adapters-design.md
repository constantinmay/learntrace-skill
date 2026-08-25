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

## 修订：PR #19 Review 修复（2026-08-23）

针对 review 指出的 5 个必改项与 3 个建议项的修订记录：

1. **Codex 退出状态三态化**：无法确认退出状态时不再默认成功，summary 记为
   "结束（状态未知）"；退出码识别扩展到 JSON `metadata.exit_code`、顶层
   `exit_code` 与文本形式（"Exit code: N" / "exited with code N"）。render
   协作概览同步增加"状态未知"分类。
2. **多宿主合并可见性**：任一宿主 parsed 时，未授权
   （`host_input_not_authorized`）或授权输入无可导入事件（`host_authorized_not_found`）
   的宿主会生成明确 warning，部分成功不再表现为整体成功；多宿主场景下 warning
   location 加 `{host}.` 前缀。
3. **末行检查链补全**：末尾无换行的行不再绕过 16 MiB 单行限制与 marker 预过滤，
   全部检查通过后才按 `truncated_final_line` 容忍解析。
4. **解析阶段资源上限**：事件达到 2000 条后停止构造（不再"先构造后截断"）；
   警告最多保留 200 条（超出汇总为一条）；未配对调用跟踪上限 2000 条；各上限
   触发时均发汇总警告。事件上限语义从"保留最早"改为"流式截止"。
5. **确认阶段冲突检查**：`--confirmations` 与四个新轨迹 flag 同现时报错，与
   OpenCode 参数对称。
6. **Codex 支持范围明确**：`custom_tool_call` / `custom_tool_call_output`
   记入 `unsupported_call_type` 警告并跳过；文档注明验证版本（0.149）与支持的
   记录类型。
7. 文档内存描述同步修正（不再声称"内存与文件大小无关"，改为逐行流式 + 累积
   上限的准确描述）。
## 修订 2：PR #19 第二轮 Review 修复（2026-08-24）

针对第二轮 review 指出的 2 个必改项与可即时处理的非阻塞项：

1. **`host_authorized_not_found` 表述中立化**：`authorized_not_found` 状态在四状态
   模型中本就同时覆盖"文件不存在"与"文件存在但无可导入事件"（见
   `docs/opencode-adapter.md`），但合并层 warning 此前写成"会话文件未找到"，
   对实际存在的文件写出了错误事实。现改为中立表述："授权输入未产生可导入事件；
   文件可能不存在，或其中没有受支持的轨迹记录。"状态模型保持不变（不新增
   枚举值），避免波及 OpenCode 适配器与既有归档定位格式。
2. **截断汇总警告独立通道**：`WarningLog` 新增 `add_summary()`，
   `event_cap_reached` / `tracked_call_cap_reached` / `warning_cap_reached`
   三类汇总警告不再占用 200 条详细警告配额，即使详细警告已超限被丢弃，截断
   事实也始终可见；`add()` / `extend()` 按 code 自动路由已知汇总码，多导出
   包装层转写 warning 时汇总同样不丢失。
3. **参数大小检查字节精确化**：Codex 调用参数 64 KiB 上限此前按字符数计算，
   非 ASCII 内容可能字节数超限仍进入 JSON 解析；现按 UTF-8 字节数判断
   （字符数先做 O(1) 预筛，仅中间区间执行实际编码）。
保留为后续优化（reviewer 认可的非阻塞项）：多宿主合并阶段的 10000 条总量
上限仍在合并后截断，限制的是最终输出而非合并阶段内存；单会话 2000 条上限
已使该路径风险有界。
