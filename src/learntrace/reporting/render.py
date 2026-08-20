"""Markdown rendering for learning archives."""

from __future__ import annotations

from pathlib import Path

from learntrace.models import (
    EventKind,
    MissingInfo,
    NodeType,
    StudentConfirmation,
    TextOrMissing,
)
from learntrace.reporting.pipeline import ArchiveBundle, archive_manifest

_NODE_LABELS: dict[NodeType, str] = {
    NodeType.FOLLOW_UP: "追问深化",
    NodeType.REVISE_AI_SUGGESTION: "修改 AI 建议",
    NodeType.FIX_FAILED_APPROACH: "修复失败方案",
    NodeType.ADD_TESTS: "补充测试",
    NodeType.ADJUST_CONSTRAINTS: "调整约束",
}


def _render_text(value: TextOrMissing) -> str:
    if isinstance(value, MissingInfo):
        return "未记录"
    return value


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
    return sum(candidate.status.value == "proposed" for candidate in bundle.candidates)


def _inference_mode_lines(bundle: ArchiveBundle) -> list[str]:
    if bundle.inference_mode == "llm":
        return [
            "- 本次候选包含远程 LLM 推断，输出在进入档案前经过 schema 校验。",
            "- AI 类候选必须至少绑定一条 `trace_record`，避免把推断直接写成事实。",
        ]
    if bundle.inference_mode == "stub":
        return [
            "- 本次候选由本地确定性规则生成，未启用远程 LLM 推断。",
            "- 候选仍保持在 `candidate_inference` 层，不会自动写成 `observable_fact`。",
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
            f"- 证据目录：{source_dir if source_dir is not None else '未记录'}",
            f"- 可观察事实：{len(bundle.events)} 条",
            f"- 学习节点候选：{len(bundle.candidates)} 条",
            f"- 学生确认：{len(bundle.confirmations)} 条",
            "",
        ]
    )

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
            lines.append(f"- {warning.code} [{warning.source}]：{warning.message}")
    else:
        lines.append("- 未记录")
    lines.append("")

    lines.append("## AI 使用")
    trace_events = [event for event in bundle.events if event.kind == EventKind.TRACE_RECORD]
    if trace_events:
        for event in trace_events:
            lines.append(f"- {event.id}：{event.summary}")
    else:
        lines.append("- 未见授权轨迹，未据此推断 AI 使用。")
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
                lines.append(f"- 候选 {candidate.id}：{candidate.statement}")
                lines.append(f"- 依据事实：{', '.join(candidate.basis_event_ids)}")
                lines.append(f"- 不确定性：{candidate.uncertainty}")
                lines.append(f"- 提问：{_render_text(candidate.question_to_student)}")
                lines.append(f"- 学生确认：{_render_confirmation_line(confirmation)}")
            lines.append("")
    else:
        lines.append("- 当前证据未形成可提问的学习节点候选。")
        lines.append("")

    lines.append("## 验证证据")
    for event in bundle.events:
        refs = ", ".join(f"{ref.type.value}:{ref.ref}" for ref in event.source_refs)
        lines.append(f"- {event.id} [{event.kind.value}]：{event.summary}（来源：{refs}）")
    lines.append("")

    lines.append("## 个人反思")
    reflection_lines = [
        f"- {_NODE_LABELS[candidate.node_type]}：{_render_text(confirmation.student_statement)}"
        for candidate in bundle.candidates
        if (confirmation := confirmations_by_candidate.get(candidate.id)) is not None
        and not isinstance(confirmation.student_statement, MissingInfo)
    ]
    lines.extend(reflection_lines or ["- 未记录"])
    lines.append("")

    lines.append("## 后续学习")
    next_steps: list[str] = []
    for candidate in bundle.candidates:
        confirmation = confirmations_by_candidate.get(candidate.id)
        if confirmation is None:
            next_steps.append(
                f"- 待补充 {candidate.id}：{_render_text(candidate.question_to_student)}"
            )
        elif isinstance(confirmation.student_statement, MissingInfo):
            next_steps.append(
                f"- 待补充 {candidate.id} 的学生说明：{_render_text(candidate.question_to_student)}"
            )
    lines.extend(next_steps or ["- 未记录"])
    lines.append("")

    lines.extend(
        [
            "## AI 使用声明",
            f"- 候选生成模式：`{bundle.inference_mode}`",
            "- `observable_fact` 仅来自 Git、文档、测试日志和授权轨迹。",
            *_inference_mode_lines(bundle),
            "- 学生未说明的内容一律展示为“未记录”。",
            "",
        ]
    )
    return "\n".join(lines)


def render_questions_markdown(bundle: ArchiveBundle) -> str:
    pending = [candidate for candidate in bundle.candidates if candidate.status.value == "proposed"]
    lines: list[str] = ["# 学生确认问题", ""]
    if not pending:
        lines.extend(["- 无待确认问题。", ""])
        return "\n".join(lines)

    for candidate in pending:
        lines.append(f"## {candidate.id}")
        lines.append(f"- 类型：{candidate.node_type.value}")
        lines.append(f"- 问题：{_render_text(candidate.question_to_student)}")
        lines.append(f"- 依据事实：{', '.join(candidate.basis_event_ids)}")
        lines.append(f"- 不确定性：{candidate.uncertainty}")
        lines.append("")
    return "\n".join(lines)
