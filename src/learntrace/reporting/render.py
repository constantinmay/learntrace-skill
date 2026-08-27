"""Markdown rendering for learning archives."""

from __future__ import annotations

import re
from pathlib import Path

from learntrace.models import (
    EventKind,
    MissingInfo,
    NodeType,
    ObservableEvent,
    StudentConfirmation,
    TextOrMissing,
)
from learntrace.privacy import normalize_project_path, redact_sensitive_text
from learntrace.reporting.pipeline import (
    ArchiveBundle,
    archive_manifest,
    fallback_reflection_questions,
    time_proximity_reflection_questions,
)

_NODE_LABELS: dict[NodeType, str] = {
    NodeType.FOLLOW_UP: "追问深化",
    NodeType.REVISE_AI_SUGGESTION: "修改 AI 建议",
    NodeType.FIX_FAILED_APPROACH: "修复失败方案",
    NodeType.ADD_TESTS: "补充测试",
    NodeType.ADJUST_CONSTRAINTS: "调整约束",
}
_GOAL_SOURCE_MARKERS = ("readme", "assignment", "requirement", "task", "任务", "要求")
_GOAL_SUMMARY_MARKERS = ("目标", "任务", "要求", "goal", "objective", "requirement")
_GOAL_SECTION_MARKERS = (
    "功能特性",
    "项目简介",
    "项目概述",
    "项目范围",
    "features",
    "overview",
    "scope",
)
_TRACE_EXCLUSION_TERMS = (
    "[absolute-path]",
    "[outside-project]",
    "[unsafe-path]",
    # Do not report the host's work on this Skill itself as evidence about the
    # student project. These are stable package identities, not user paths.
    "learntrace-skill",
    "skills/learntrace",
    "命令类型：git add",
    "命令类型：git log",
    "命令类型：git status",
    "命令类型：kill",
    "命令类型：ls",
    "命令类型：node",
    "命令类型：pgrep",
    "命令类型：pkill",
    "命令类型：rm",
    "命令类型：ss",
    "工具 todowrite",
    "unknown-command",
    "工具 unknown",
)
_MAX_RENDERED_TRACE_EVENTS = 20
_MAX_PROGRESS_ITEMS = 8
_COMMIT_PREFIX_RE = re.compile(
    r"^(?:提交|commit)\s+[a-f0-9]+\s*(?:[：:]|的提交信息为)\s*",
    re.IGNORECASE,
)
_TRACE_TOOL_RE = re.compile(r"(?:OpenCode|Claude Code|Codex) 工具 (?P<tool>[A-Za-z0-9_.-]+)")
_HOME_PATH_RE = re.compile(
    r"(?i)(?<!\w)(?:~[^\\/\s]*|\$(?:HOME|USERPROFILE|HOMEPATH)|"
    r"\$\{(?:HOME|USERPROFILE|HOMEPATH)\}|\$env:(?:HOME|USERPROFILE|HOMEPATH)|"
    r"%(?:HOME|USERPROFILE|HOMEPATH)%)(?:[\\/][^\s，。；、\"'`]+)+"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"((?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s，。；、\"'`]+|"
    r"\\\\[^\\/\s]+[\\/][^\s，。；、\"'`]+|"
    r"(?<![\w:/])/(?!api(?:/|\b)|v\d+(?:/|\b))[^\s，。；、\"'`]+)"
)
_SOURCE_LINE_SUFFIX_RE = re.compile(r":\d+(?:-\d+)?$")


def _render_safe_text(value: str) -> str:
    """Remove secrets and host paths at the final shareable-Markdown boundary."""
    text = redact_sensitive_text(value, limit=len(value) or 1)
    text = _HOME_PATH_RE.sub("[private-path]", text)
    return _ABSOLUTE_PATH_RE.sub("[absolute-path]", text)


def _render_text(value: TextOrMissing) -> str:
    if isinstance(value, MissingInfo):
        return "未记录"
    return _render_safe_text(value)


def _source_ref_path(ref: str) -> str:
    """Remove a trailing line range without splitting a Windows drive prefix."""
    return _SOURCE_LINE_SUFFIX_RE.sub("", ref).replace("\\", "/")


def _render_confirmation_line(confirmation: StudentConfirmation | None) -> str:
    if confirmation is None:
        return "未记录"
    return f"{confirmation.decision.value}；{_render_text(confirmation.student_statement)}"


def _missing_info_count(bundle: ArchiveBundle) -> int:
    candidate_missing = sum(
        isinstance(candidate.question_to_student, MissingInfo) for candidate in bundle.candidates
    )
    confirmation_missing = sum(
        isinstance(confirmation.student_statement, MissingInfo)
        for confirmation in bundle.confirmations
    )
    return candidate_missing + confirmation_missing


def _pending_question_count(bundle: ArchiveBundle) -> int:
    candidate_questions = sum(
        candidate.status.value == "proposed" for candidate in bundle.candidates
    )
    return (
        candidate_questions
        + len(fallback_reflection_questions(bundle))
        + len(time_proximity_reflection_questions(bundle))
    )


def _source_dir_label(source_dir: Path | None) -> str:
    """Render a portable evidence-root label without exposing a host path."""
    if source_dir is None:
        return "未记录"
    if source_dir.is_absolute():
        return "."
    value = normalize_project_path(str(source_dir), None)
    if value.startswith("[") and value.endswith("]"):
        return "未记录"
    return value


def _project_goal_lines(bundle: ArchiveBundle) -> list[str]:
    candidates: list[ObservableEvent] = []
    for event in bundle.events:
        if event.kind != EventKind.DOCUMENT:
            continue
        source_paths = [_source_ref_path(ref.ref) for ref in event.source_refs]
        root_sources = [path for path in source_paths if "/" not in path.strip("/")]
        refs = " ".join(path.casefold() for path in root_sources)
        summary = event.summary.casefold()
        if root_sources and (
            any(marker in refs for marker in _GOAL_SOURCE_MARKERS)
            or any(marker in summary for marker in _GOAL_SUMMARY_MARKERS)
        ):
            candidates.append(event)
    if not candidates:
        return ["- 未记录；请由学生根据课程任务或项目 README 补充。"]

    def source_line(event: ObservableEvent) -> int:
        line_numbers: list[int] = []
        for ref in event.source_refs:
            match = re.search(r":(?P<line>\d+)(?:-|$)", ref.ref)
            if match is not None:
                line_numbers.append(int(match.group("line")))
        return min(line_numbers, default=10**9)

    lines: list[str] = []
    seen_documents: set[str] = set()
    for event in sorted(candidates, key=lambda item: (source_line(item), item.id)):
        root_sources = sorted(
            _source_ref_path(ref.ref)
            for ref in event.source_refs
            if "/" not in _source_ref_path(ref.ref).strip("/")
        )
        document_key = root_sources[0].casefold() if root_sources else event.id
        is_first_section = document_key not in seen_documents
        seen_documents.add(document_key)
        lowered_summary = event.summary.casefold()
        if not is_first_section and not any(
            marker.casefold() in lowered_summary
            for marker in (*_GOAL_SUMMARY_MARKERS, *_GOAL_SECTION_MARKERS)
        ):
            continue
        text = _render_safe_text(event.summary)
        text = re.sub(r"```.*?```|~~~.*?~~~", "", text, flags=re.DOTALL)
        # Parser chunks may start in a Markdown table/code block. Such
        # fragments are not a reliable project-goal statement.
        fragments = [
            fragment.strip()
            for fragment in re.split(r"[\r\n]+", text)
            if fragment.strip() and not fragment.lstrip().startswith(("|", "```", "~~~"))
        ]
        cleaned = " ".join(fragments)
        recorded_content = cleaned.rsplit("记录：", maxsplit=1)[-1].strip()
        if cleaned and recorded_content and not recorded_content.startswith("|"):
            lines.append(f"- {cleaned[:240]}")
        if len(lines) == 3:
            break
    return lines or ["- 未记录；请由学生根据课程任务或项目 README 补充。"]


def _trace_search_text(event: ObservableEvent) -> str:
    refs = " ".join(f"{source.ref} {source.note or ''}" for source in event.source_refs)
    return f"{event.summary} {refs}".casefold()


def _relevant_trace_events(bundle: ArchiveBundle) -> tuple[list[ObservableEvent], int]:
    trace_events = [event for event in bundle.events if event.kind == EventKind.TRACE_RECORD]
    relevant: list[ObservableEvent] = []
    for event in trace_events:
        searchable = _trace_search_text(event)
        if any(term.casefold() in searchable for term in _TRACE_EXCLUSION_TERMS):
            continue
        # The audit appendix keeps only semantically meaningful traces. The
        # human-facing AI collaboration overview above aggregates completed
        # operations separately, so they are represented without flooding the
        # report with per-event identifiers.
        if searchable.startswith(("opencode 工具", "claude code 工具", "codex 工具")) and (
            "已完成" in searchable
        ):
            continue
        relevant.append(event)
    visible = relevant[:_MAX_RENDERED_TRACE_EVENTS]
    return visible, len(trace_events) - len(visible)


def _project_progress_lines(bundle: ArchiveBundle) -> list[str]:
    commits = [event for event in bundle.events if event.kind == EventKind.GIT_COMMIT]
    overview = [event for event in commits if _COMMIT_PREFIX_RE.match(event.summary)]
    selected = overview or commits
    if not selected:
        return ["- 当前材料没有可用于概括项目进展的提交记录。"]
    lines: list[str] = []
    for index, event in enumerate(selected[:_MAX_PROGRESS_ITEMS], start=1):
        summary = _COMMIT_PREFIX_RE.sub("", event.summary).strip(" ：:")
        lines.append(f"- 阶段 {index}：{_render_safe_text(summary)}")
    if len(selected) > _MAX_PROGRESS_ITEMS:
        lines.append(f"- 其余 {len(selected) - _MAX_PROGRESS_ITEMS} 条提交保存在机器审计档案中。")
    return lines


def _ai_collaboration_lines(bundle: ArchiveBundle) -> list[str]:
    traces = [event for event in bundle.events if event.kind == EventKind.TRACE_RECORD]
    counts: dict[str, dict[str, int]] = {}
    excluded = 0
    for event in traces:
        searchable = _trace_search_text(event)
        if any(term.casefold() in searchable for term in _TRACE_EXCLUSION_TERMS):
            excluded += 1
            continue
        match = _TRACE_TOOL_RE.search(event.summary)
        tool = match.group("tool") if match else "其他工具"
        if "未完成" in event.summary or "incomplete" in event.summary.casefold():
            status = "incomplete"
        elif "状态未知" in event.summary:
            status = "unknown"
        elif "错误" in event.summary or "error" in event.summary.casefold():
            status = "error"
        else:
            status = "completed"
        tool_counts = counts.setdefault(
            tool,
            {"completed": 0, "error": 0, "incomplete": 0, "unknown": 0},
        )
        tool_counts[status] += 1
    if not counts:
        return ["- 未见与本项目范围相关的授权 AI 工具轨迹。"]
    lines: list[str] = []
    for tool, values in sorted(
        counts.items(),
        key=lambda item: (
            -(item[1]["completed"] + item[1]["error"] + item[1]["incomplete"] + item[1]["unknown"]),
            item[0],
        ),
    ):
        total = values["completed"] + values["error"] + values["incomplete"] + values["unknown"]
        lines.append(
            f"- {tool}：共 {total} 次，完成 {values['completed']} 次，"
            f"错误 {values['error']} 次，未完成 {values['incomplete']} 次，"
            f"状态未知 {values['unknown']} 次。"
        )
    if excluded:
        lines.append(f"- 另有 {excluded} 条越界或低信号轨迹未纳入协作概览。")
    return lines


def _inference_mode_lines(bundle: ArchiveBundle) -> list[str]:
    if bundle.inference_mode == "stub":
        return [
            "- 本次候选由本地确定性规则生成。",
            "- 候选仍保持在 `candidate_inference` 层，不会自动写成 `observable_fact`。",
        ]
    if bundle.inference_mode in ("llm", "llm_stub_fallback"):
        return [
            f"- 历史记录：该档案生成于早期版本（候选模式：`{bundle.inference_mode}`）。",
            "- 当前 CLI 只使用本地确定性规则，重新渲染不会重新发起任何远程调用。",
            "- 无论历史推断器来源如何，候选都不会自动上升为事实记录。",
        ]
    return [
        f"- 本次候选由自定义推断器生成（模式：`{bundle.inference_mode}`）。",
        "- 无论推断器来源如何，候选都不会自动上升为事实记录。",
    ]


def render_markdown(bundle: ArchiveBundle, *, source_dir: Path | None = None) -> str:
    confirmations_by_candidate = {
        confirmation.candidate_id: confirmation for confirmation in bundle.confirmations
    }
    manifest = archive_manifest(bundle)
    lines: list[str] = ["# 学习档案", ""]

    lines.extend(
        [
            "## 项目概览",
            f"- 证据目录：{_source_dir_label(source_dir)}",
            f"- 可观察事实：{len(bundle.events)} 条",
            f"- 学习节点候选：{len(bundle.candidates)} 条",
            f"- 学生确认：{len(bundle.confirmations)} 条",
            "",
        ]
    )

    lines.append("## 项目目标")
    lines.extend(_project_goal_lines(bundle))
    lines.append("")

    lines.append("## 项目进展")
    lines.extend(_project_progress_lines(bundle))
    lines.append("")

    lines.append("## 学习收获")
    if bundle.candidates:
        for candidate in bundle.candidates:
            confirmation = confirmations_by_candidate.get(candidate.id)
            if confirmation is None:
                label = "学习线索（待学生确认）"
            elif confirmation.decision.value == "denied":
                label = "系统线索（学生已否认）"
            elif confirmation.decision.value == "supplemented":
                label = "学习线索（学生已补充）"
            else:
                label = "学习线索（学生已确认）"
            lines.append(f"- {label}：{_render_safe_text(candidate.statement)}")
            if confirmation is not None:
                lines.append(f"  - 学生原话：{_render_text(confirmation.student_statement)}")
            else:
                lines.append(f"  - 待回答：{_render_text(candidate.question_to_student)}")
    else:
        lines.append("- 当前证据未形成明确学习结论；系统不会为填满档案而编造内容。")
    lines.append("")

    lines.append("## AI 协作场景")
    lines.extend(_ai_collaboration_lines(bundle))
    lines.append("")

    lines.append("## 个人反思")
    lines.append("- 未记录；本节必须由学生本人填写，系统不会用确认陈述代写反思。")
    lines.append("<!-- 可填写：我学到了什么、为何改变方案、哪些判断仍需验证。 -->")
    lines.append("")

    lines.append("## 后续学习")
    reflection_questions = (
        *fallback_reflection_questions(bundle),
        *time_proximity_reflection_questions(bundle),
    )
    next_steps: list[str] = []
    for candidate in bundle.candidates:
        confirmation = confirmations_by_candidate.get(candidate.id)
        if confirmation is None or isinstance(confirmation.student_statement, MissingInfo):
            next_steps.append(f"- {_render_text(candidate.question_to_student)}")
    next_steps.extend(
        f"- {_render_safe_text(str(question['question_to_student']))}"
        for question in reflection_questions
    )
    lines.extend(next_steps or ["- 未记录；请由学生本人填写下一步学习或验证计划。"])
    lines.append("")

    lines.extend(["<details>", "<summary>机器审计附录</summary>", ""])

    lines.extend(
        [
            "## 审计摘要",
            f"- 档案指纹：`{manifest['hash_algorithm']}:{manifest['content_fingerprint']}`",
            f"- Schema：`{manifest['schema_version']}`",
            f"- 生成工具：`{manifest['tool']} {manifest['tool_version']}`",
            "- 指纹仅基于事实、候选、确认与解析告警生成，不包含本机路径或生成时间。",
            "",
        ]
    )

    lines.extend(
        [
            "## 证据分层",
            f"- observable_fact：{len(bundle.events)} 条",
            f"- candidate_inference：{len(bundle.candidates)} 条",
            f"- student_confirmation：{len(bundle.confirmations)} 条",
            f"- missing_info：{_missing_info_count(bundle)} 处",
            f"- 待确认问题：{_pending_question_count(bundle)} 条",
            "",
        ]
    )

    lines.append("## 解析告警与边界")
    if bundle.warnings:
        for warning in bundle.warnings:
            source = _render_safe_text(warning.source)
            message = _render_safe_text(warning.message)
            lines.append(f"- {warning.code} [{source}]：{message}")
    else:
        lines.append("- 未记录")
    lines.append("")

    lines.append("## AI 使用")
    trace_events, omitted_trace_count = _relevant_trace_events(bundle)
    if trace_events:
        for event in trace_events:
            lines.append(f"- {event.id}：{_render_safe_text(event.summary)}")
    elif omitted_trace_count:
        lines.append("- 授权轨迹仅包含通用工具操作，已在协作概览中汇总；未据此推断学习结论。")
    else:
        lines.append("- 未见与本项目范围相关的授权轨迹，未据此推断 AI 使用。")
    if omitted_trace_count:
        lines.append(f"- 已过滤或省略 {omitted_trace_count} 条越界、低信号或超出展示上限的轨迹。")
    lines.append("")

    lines.append("## 关键决策")
    if bundle.candidates:
        for node_type in NodeType:
            matching = [
                candidate for candidate in bundle.candidates if candidate.node_type == node_type
            ]
            if not matching:
                continue
            lines.append(f"### {_NODE_LABELS[node_type]}")
            for candidate in matching:
                confirmation = confirmations_by_candidate.get(candidate.id)
                lines.append(f"- 候选 {candidate.id}：{_render_safe_text(candidate.statement)}")
                lines.append(f"- 依据事实：{', '.join(candidate.basis_event_ids)}")
                lines.append(f"- 不确定性：{_render_safe_text(candidate.uncertainty)}")
                lines.append(f"- 提问：{_render_text(candidate.question_to_student)}")
                lines.append(f"- 学生确认：{_render_confirmation_line(confirmation)}")
            lines.append("")
    else:
        lines.append("- 当前证据未形成可提问的学习节点候选。")
        lines.append("")

    lines.append("## 验证证据")
    test_logs = [event for event in bundle.events if event.kind == EventKind.TEST_LOG]
    if not test_logs:
        lines.append("- 未发现测试日志；当前档案不能证明项目测试已运行或通过。")

    basis_event_ids = {
        event_id for candidate in bundle.candidates for event_id in candidate.basis_event_ids
    }
    evidence_events = [
        event
        for event in bundle.events
        if event.kind == EventKind.TEST_LOG or event.id in basis_event_ids
    ]
    for event in evidence_events:
        refs = ", ".join(
            f"{ref.type.value}:{_render_safe_text(ref.ref)}" for ref in event.source_refs
        )
        summary = _render_safe_text(event.summary)
        lines.append(f"- {event.id} [{event.kind.value}]：{summary}（来源：{refs}）")
    if not evidence_events:
        lines.append("- 当前没有与候选直接关联的验证证据。")
    lines.append("- 完整事实与来源索引保存在 `.learntrace/archive-records.json`。")
    lines.append("")

    lines.extend(
        [
            "## AI 使用声明",
            f"- 候选生成模式：`{bundle.inference_mode}`",
            "- `observable_fact` 仅来自 Git、文档、测试日志和授权轨迹。",
            *_inference_mode_lines(bundle),
            "- 学生未说明的内容一律展示为“未记录”。",
            "",
            "</details>",
            "",
        ]
    )
    return "\n".join(lines)


def render_questions_markdown(bundle: ArchiveBundle) -> str:
    pending = [candidate for candidate in bundle.candidates if candidate.status.value == "proposed"]
    fallback = (
        *fallback_reflection_questions(bundle),
        *time_proximity_reflection_questions(bundle),
    )
    lines: list[str] = ["# 学生复盘与确认问题", ""]
    if not pending and not fallback:
        lines.extend(["- 无待确认问题。", ""])
        return "\n".join(lines)

    for candidate in pending:
        lines.append(f"## {candidate.id}")
        lines.append(f"- 类型：{candidate.node_type.value}")
        lines.append(f"- 问题：{_render_text(candidate.question_to_student)}")
        lines.append(f"- 依据事实：{', '.join(candidate.basis_event_ids)}")
        lines.append(f"- 不确定性：{_render_safe_text(candidate.uncertainty)}")
        lines.append("")
    for question in fallback:
        lines.append(f"## {question['question_id']}")
        lines.append(f"- 类型：{question['question_type']}")
        lines.append(f"- 问题：{_render_safe_text(str(question['question_to_student']))}")
        lines.append(f"- 说明：{_render_safe_text(str(question['uncertainty']))}")
        lines.append("")
    return "\n".join(lines)
