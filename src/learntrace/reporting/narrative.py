"""叙事层:payload 校验(三条红线)与 CLI 渲染器(Issue #26)。

四层分工中的边界在此实现:

- 叙事层:宿主 agent 产出叙事 payload(JSON,``narrative-payload.schema.json``),
  只填写叙述文本与引用,不直接产 Markdown;
- 校验层:``verify_payload`` 以三条确定性红线校验 payload(引用可解析、
  被否认线索不入正文、必需字段不缺),不重跑候选推断;
- 呈现层:``render_narrative_markdown`` 按固定章节骨架渲染 working /
  submitted 两个版本。渲染器是纯函数,事实层三节(项目概述/开发轨迹/阶段详情)
  对两个版本走同一代码路径,因此逐字一致;
- 反思层:``reflection`` 恒为 ``student_authored_only``,working 版必须为
  null(系统不代写),submitted 版必须由本人填写。

内部事件 ID 只出现在脚注定义与 ``原始提交`` 列表中,正文只引用语义脚注标签。
所有进入 Markdown 的文本都经过与既有报告相同的脱敏边界(``render_safe_text``)。
本模块无任何网络调用;候选推断仍由宿主 agent 负责,与确定性层互验。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

from learntrace.models.validation import ContractValidator
from learntrace.reporting.render import render_safe_text

NarrativeDict = dict[str, Any]
ArchiveDict = dict[str, Any]
# 事件 ID 形如 evt-<来源>-<标识>(如 evt-git-1c6bb73e、evt-trace-177ed54bffeYQO)。
# 面向读者的描述文本中出现档案内真实事件 ID(包括空白/反引号包裹或嵌入句中),
# 说明引用被错写进内容,渲染会把裸 ID 直接呈现给读者。外形相似但不在档案中的
# 领域标识(如项目自定义的 evt-order-created)不属于内部 ID,不得误拦。
_EVENT_ID_IN_TEXT_RE = re.compile(r"evt-[A-Za-z]+-[0-9A-Za-z]+")


def _as_dict(value: Any) -> NarrativeDict | None:
    """strict 下 isinstance(dict) 对 Any 收窄为未知泛型,边界处显式 cast。"""
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


def _as_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return cast("list[Any]", value)
    return None


def _field(mapping: NarrativeDict, key: str) -> NarrativeDict:
    value = _as_dict(mapping.get(key))
    return value if value is not None else {}


def _list_field(mapping: NarrativeDict, key: str) -> list[Any]:
    value = _as_list(mapping.get(key))
    return value if value is not None else []


_CONFIRMED_OR_SUPPLEMENTED = ("confirmed", "supplemented")
_KIND_HEADINGS = {
    "Added": "Added",
    "Removed": "Removed",
    "Fixed": "Fixed",
    "Changed": "Changed",
    "Note": "",
    "Collaboration": "协作边界",
}
_WORKING_INTRO_LINES = (
    "本文档由 LearnTrace 依据 Git 提交、项目文档和经授权的会话轨迹生成。",
    "标注「推断」的内容未经本人确认;「未记录」表示当前证据中不存在对应信息。",
    "你可以修改、否认或删除任何部分;机器审计细节保存在 `.learntrace/archive-records.json`。",
)
_SCOPE_INTRO_LINES = (
    "**范围说明**:本仓库为多人协作项目,本档案仅纳入本人名下提交与授权轨迹;",
    "协作方实现的内容不在档案范围内,相关阶段仅标注协作边界,不作归属评价。",
)
_TURNING_POINT_STATUS_LABELS = {
    "confirmed": "已被本人确认",
    "supplemented": "已被本人补充纠正",
}


def load_payload(path: Path) -> NarrativeDict:
    """读取叙事 payload JSON 文件。"""
    with path.open(encoding="utf-8") as handle:
        payload: Any = json.load(handle)
    if not isinstance(payload, dict):
        msg = f"叙事 payload 必须是 JSON 对象: {path}"
        raise ValueError(msg)
    return cast("dict[str, Any]", payload)


def load_archive(path: Path) -> ArchiveDict:
    """读取档案记录 JSON 文件(``bundle_to_dict`` 的输出,即 ``archive-records.json``)。"""
    with path.open(encoding="utf-8") as handle:
        archive: Any = json.load(handle)
    if not isinstance(archive, dict):
        msg = f"档案记录必须是 JSON 对象: {path}"
        raise ValueError(msg)
    return cast("dict[str, Any]", archive)


def _citation_event_id(citation: Any) -> str:
    if isinstance(citation, str) and citation:
        return citation
    mapping = _as_dict(citation)
    if mapping is not None:
        event_id = mapping.get("event_id")
        if isinstance(event_id, str) and event_id:
            return event_id
    msg = f"引用必须是事件 ID 字符串或含 event_id 的对象: {citation!r}"
    raise ValueError(msg)


def _citation_explicit_label(citation: Any) -> str | None:
    mapping = _as_dict(citation)
    if mapping is not None:
        label = mapping.get("label")
        if isinstance(label, str) and label:
            return label
    return None


def _auto_label(event_id: str) -> str:
    """裸事件 ID 的确定性脚注标签:git 提交用 7 位短哈希,其余用 7 位 ID 后缀。"""
    if event_id.startswith("evt-git-"):
        return f"c-{event_id[8:15]}"
    if event_id.startswith("evt-doc-"):
        return f"doc-{event_id[8:15]}"
    return f"t-{event_id.rsplit('-', 1)[-1][:7]}"


def _iter_citation_locations(payload: NarrativeDict) -> list[tuple[str, Any]]:
    """按文档顺序列出 payload 中全部 (位置, 引用) 对。"""
    pairs: list[tuple[str, Any]] = []
    overview = _as_dict(payload.get("overview"))
    if overview is not None:
        for index, citation in enumerate(overview.get("citations") or []):
            pairs.append((f"overview.citations[{index}]", citation))
    for s_index, stage in enumerate(payload.get("stages") or []):
        for index, citation in enumerate(stage.get("citations") or []):
            pairs.append((f"stages[{s_index}].citations[{index}]", citation))
        for c_index, change in enumerate(stage.get("key_changes") or []):
            change_dict = _as_dict(change)
            if change_dict is not None:
                for index, citation in enumerate(change_dict.get("citations") or []):
                    pairs.append(
                        (
                            f"stages[{s_index}].key_changes[{c_index}].citations[{index}]",
                            citation,
                        )
                    )
        anchor = stage.get("merge_anchor")
        if isinstance(anchor, str):
            pairs.append((f"stages[{s_index}].merge_anchor", anchor))
        else:
            anchor_list = _as_list(anchor)
            if anchor_list is not None:
                for index, citation in enumerate(anchor_list):
                    pairs.append((f"stages[{s_index}].merge_anchor[{index}]", citation))
    for index, turning in enumerate(payload.get("turning_points") or []):
        for c_index, citation in enumerate(turning.get("citations") or []):
            pairs.append((f"turning_points[{index}].citations[{c_index}]", citation))
    ai = _as_dict(payload.get("ai_collaboration"))
    if ai is not None:
        for e_index, episode in enumerate(ai.get("episodes") or []):
            for c_index, citation in enumerate(episode.get("citations") or []):
                pairs.append(
                    (f"ai_collaboration.episodes[{e_index}].citations[{c_index}]", citation)
                )
    return pairs


def _build_label_map(payload: NarrativeDict) -> tuple[list[str], dict[str, list[str]]]:
    """按首次出现顺序建立 脚注标签 -> 事件 ID 列表 的映射(同标签合并)。"""
    order: list[str] = []
    mapping: dict[str, list[str]] = {}
    for _, citation in _iter_citation_locations(payload):
        event_id = _citation_event_id(citation)
        label = _citation_explicit_label(citation) or _auto_label(event_id)
        if label not in mapping:
            mapping[label] = []
            order.append(label)
        if event_id not in mapping[label]:
            mapping[label].append(event_id)
    return order, mapping


def _denied_basis_event_ids(archive: ArchiveDict) -> set[str]:
    """红线 2 的联结:被本人否认的候选的 basis 事件集合。"""
    candidates_by_id: dict[str, NarrativeDict] = {}
    for candidate in _list_field(archive, "candidates"):
        candidate_dict = _as_dict(candidate)
        if candidate_dict is not None:
            candidates_by_id[str(candidate_dict.get("id"))] = candidate_dict
    denied: set[str] = set()
    for confirmation in _list_field(archive, "confirmations"):
        confirmation_dict = _as_dict(confirmation)
        if confirmation_dict is None:
            continue
        if confirmation_dict.get("decision") != "denied":
            continue
        candidate = candidates_by_id.get(str(confirmation_dict.get("candidate_id")))
        if candidate is not None:
            for event_id in _list_field(candidate, "basis_event_ids"):
                denied.add(str(event_id))
    return denied


def verify_payload(payload: NarrativeDict, archive: ArchiveDict) -> list[str]:
    """校验叙事 payload,返回违规列表(空列表 = 通过)。

    三条红线:
    1. payload 中的引用必须解析到档案内的可观察事件;
    2. 被本人否认候选的 basis 事件不得出现在任何正文引用位置
       (overview/stages/key_changes/merge_anchor/转折/AI 插曲);
       denied 线索只允许留在 denied_kept_in_appendix 槽的引用里
       (渲染时仅进附录);submitted 版不得保留 denied 槽;
    3. 必需字段不缺:meta 计数与档案一致,working 版反思必须为 null,
       submitted 版必须由本人填写开篇/AI 声明/收获与反思。
    另先做 schema 校验(纯新增的 ``narrative_payload`` 记录类型)。
    """
    violations: list[str] = []
    validator = ContractValidator()
    for error in validator.iter_errors("narrative_payload", payload):
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        violations.append(f"schema:{location}: {error.message}")
    if violations:
        return violations

    event_ids = {
        str(event_dict.get("id"))
        for event in _list_field(archive, "events")
        if (event_dict := _as_dict(event)) is not None
    }

    # 红线 1:引用可解析。
    for location, citation in _iter_citation_locations(payload):
        try:
            event_id = _citation_event_id(citation)
        except ValueError as exc:
            violations.append(f"红线1:{location}: {exc}")
            continue
        if event_id not in event_ids:
            violations.append(f"红线1:{location}: 引用 {event_id} 无法解析到档案事件")
    # 字符串形式的关键变更是面向读者的描述文本,不是引用位置;
    # 出现事件 ID 说明引用被错写进内容,渲染会把裸 ID 直接呈现给读者。
    for s_index, stage in enumerate(payload.get("stages") or []):
        stage_dict = _as_dict(stage)
        if stage_dict is None:
            continue
        for c_index, change in enumerate(_list_field(stage_dict, "key_changes")):
            # 字符串形式与对象形式的 text 都是面向读者的描述文本,不是引用位置。
            if isinstance(change, str):
                text = change
            elif (change_dict := _as_dict(change)) is not None and isinstance(
                change_dict.get("text"), str
            ):
                text = change_dict["text"]
            else:
                continue
            mentioned = [
                event_id for event_id in _EVENT_ID_IN_TEXT_RE.findall(text) if event_id in event_ids
            ]
            if mentioned:
                violations.append(
                    f"红线1:stages[{s_index}].key_changes[{c_index}]: "
                    f"关键变更必须是描述文本,事件 ID {mentioned[0]} 请移入 citations"
                )

    # 红线 2:被否认线索不入正文。
    denied = _denied_basis_event_ids(archive)
    variant = payload.get("variant")
    allowed: list[str] = []
    for index, turning_raw in enumerate(payload.get("turning_points") or []):
        turning = _as_dict(turning_raw)
        if turning is None:
            continue
        status = turning.get("status")
        if status == "denied_kept_in_appendix":
            # denied 槽的 citations 是唯一体外例外:线索留在附录备查,不算正文引用。
            for c_index in range(len(_list_field(turning, "citations"))):
                allowed.append(f"turning_points[{index}].citations[{c_index}]")
            if variant == "submitted":
                violations.append(
                    f"红线2:turning_points[{index}]:submitted 版不得保留 denied_kept_in_appendix 槽"
                )
    for location, citation in _iter_citation_locations(payload):
        if location in allowed:
            continue
        try:
            event_id = _citation_event_id(citation)
        except ValueError:
            continue  # 格式问题已由红线 1 报告
        if event_id in denied:
            violations.append(f"红线2:{location}: 引用了被本人否认线索的 basis 事件 {event_id}")

    # 红线 3:必需字段不缺、计数一致、反思层归属学生。
    meta = _as_dict(payload.get("meta"))
    if meta is None:
        violations.append("红线3:meta 必须是对象")
    else:
        if meta.get("evidence_gaps") != len(payload.get("evidence_gaps") or []):
            violations.append("红线3:meta.evidence_gaps 与 evidence_gaps 数量不一致")
        if meta.get("pending_questions") != len(archive.get("pending_questions") or []):
            violations.append("红线3:meta.pending_questions 与档案 pending_questions 数量不一致")
    reflection = _as_dict(payload.get("reflection"))
    reflection_text = reflection.get("text") if reflection is not None else None
    if variant == "working" and reflection_text is not None:
        violations.append("红线3:working 版 reflection.text 必须为 null(系统不代写反思)")
    if variant == "submitted":
        if not payload.get("intro_note"):
            violations.append("红线3:submitted 版必须有 intro_note(第一人称开篇)")
        if not payload.get("ai_statement"):
            violations.append("红线3:submitted 版必须有 ai_statement(AI 使用声明)")
        if not payload.get("takeaways"):
            violations.append("红线3:submitted 版 takeaways 不能为空(学习收获由本人填写)")
        if not isinstance(reflection_text, str) or not reflection_text:
            violations.append("红线3:submitted 版 reflection.text 必须由本人填写")

    return violations


def _safe(value: Any) -> str:
    return render_safe_text(str(value))


def _stage_number(stage: NarrativeDict) -> str:
    return str(stage.get("stage_id", "")).removeprefix("stage-")


def _stage_citations_ordered(stage: NarrativeDict) -> list[Any]:
    ordered: list[Any] = list(_list_field(stage, "citations"))
    for change in _list_field(stage, "key_changes"):
        change_dict = _as_dict(change)
        if change_dict is not None:
            ordered.extend(_list_field(change_dict, "citations"))
    anchor = stage.get("merge_anchor")
    if isinstance(anchor, str):
        ordered.append(anchor)
    else:
        anchor_list = _as_list(anchor)
        if anchor_list is not None:
            ordered.extend(anchor_list)
    return ordered


def _stage_git_events(stage: NarrativeDict, events_by_id: dict[str, NarrativeDict]) -> list[str]:
    """阶段引用的 git_commit 事件,按引用顺序去重(用于 原始提交 折叠列表)。"""
    result: list[str] = []
    for citation in _stage_citations_ordered(stage):
        try:
            event_id = _citation_event_id(citation)
        except ValueError:
            continue
        event = events_by_id.get(event_id)
        if event is not None and event.get("kind") == "git_commit" and event_id not in result:
            result.append(event_id)
    return result


def _render_key_changes(
    key_changes: Sequence[Any],
    refs_for: Callable[[list[Any]], str],
) -> list[str]:
    """阶段详情条目:带 kind 的对象按 Added/Removed/Fixed/Changed/协作边界 分组,
    纯文本条目为普通 bullet(inferred_truncated 阶段即此形态)。"""
    lines: list[str] = []
    current_heading: str | None = None
    for change in key_changes:
        change_dict = _as_dict(change)
        if change_dict is not None:
            heading = _KIND_HEADINGS.get(str(change_dict.get("kind")), "")
            if heading:
                if heading != current_heading:
                    if lines:
                        lines.append("")
                    lines.append(f"**{heading}**")
                current_heading = heading
            else:
                current_heading = None
            refs = refs_for(list(change_dict.get("citations") or []))
            lines.append(f"- {_safe(change_dict.get('text', ''))}{refs}")
        else:
            current_heading = None
            lines.append(f"- {_safe(change)}")
    return lines


def _render_verification_lines(payload: NarrativeDict) -> list[str]:
    """验证与质量小节(fact 层,variant 无关 → 两版逐字一致)。"""
    lines = ["## 验证与质量", ""]
    items = _list_field(payload, "verification")
    if items:
        for item in items:
            lines.append(f"- {_safe(item)}")
    else:
        lines.append("- 未记录")
    lines.append("")
    return lines


def _footnote_definition(
    label: str, event_ids: list[str], events_by_id: dict[str, NarrativeDict]
) -> str:
    fragments: list[str] = []
    for event_id in event_ids:
        event = events_by_id.get(event_id) or {}
        kind = event.get("kind")
        if kind == "git_commit":
            fragments.append(f"commit `{event_id[8:15]}`(archive 事件 `{event_id}`)")
        elif kind == "document":
            source_refs = _as_list(event.get("source_refs")) or []
            first_ref = _as_dict(source_refs[0]) if source_refs else None
            ref = first_ref.get("ref", "未记录") if first_ref is not None else "未记录"
            fragments.append(f"`{_safe(ref)}`(archive 事件 `{event_id}`)")
        else:
            fragments.append(f"archive 事件 `{event_id}`")
    return f"[^{label}]: " + "、".join(fragments)


def render_narrative_markdown(
    payload: NarrativeDict,
    archive: ArchiveDict,
    *,
    variant: str | None = None,
) -> str:
    """按固定章节骨架渲染叙事 Markdown(纯函数,无网络调用)。

    ``variant`` 缺省取 payload 自身版本;同一 payload 可渲染两个版本,
    事实层三节(项目概述/开发轨迹/阶段详情)输出逐字一致。
    调用前应先通过 ``verify_payload``。
    """
    selected = variant or payload.get("variant")
    if selected not in ("working", "submitted"):
        msg = f"未知渲染版本: {selected!r}(须为 working 或 submitted)"
        raise ValueError(msg)

    events_by_id: dict[str, NarrativeDict] = {}
    for event in _list_field(archive, "events"):
        event_dict = _as_dict(event)
        if event_dict is not None and event_dict.get("id"):
            events_by_id[str(event_dict.get("id"))] = event_dict
    label_order, label_map = _build_label_map(payload)

    def refs_for(citations: list[Any]) -> str:
        out: list[str] = []
        for citation in citations:
            event_id = _citation_event_id(citation)
            label = _citation_explicit_label(citation) or _auto_label(event_id)
            out.append(f"[^{label}]")
        return "".join(out)

    meta = _field(payload, "meta")
    project = _safe(meta.get("project", "未命名项目"))
    window = _safe(meta.get("evidence_window", "未记录"))
    pending_count = meta.get("pending_questions", 0)
    gap_count = meta.get("evidence_gaps", 0)
    self_scoped = payload.get("author_scope") == "self_only"
    reflection = _as_dict(payload.get("reflection"))
    reflection_text = reflection.get("text") if reflection is not None else None

    lines: list[str] = []
    if selected == "working":
        status = meta.get("status", "未确认版")
        lines.append(f"# 项目复盘:{project}({_safe(status)})")
        lines.append("")
        lines.append("| 项目 | 证据窗口 | 版本状态 | 复盘提示 | 证据缺口 |")
        lines.append("|---|---|---|---|---|")
        lines.append(
            f"| {project} | {window} | 未确认版,未经本人复核 | "
            f"{pending_count} 条可选(见 `.learntrace/learning-questions.md`) | "
            f"{gap_count} 处 |"
        )
        lines.append("")
        for intro_line in _WORKING_INTRO_LINES:
            lines.append(f"> {intro_line}")
        if self_scoped:
            for scope_line in _SCOPE_INTRO_LINES:
                lines.append(f"> {scope_line}")
    else:
        lines.append(f"# {project} — 项目复盘")
        lines.append("")
        lines.append("| 项目 | 日期 | 版本状态 | 证据可核查 |")
        lines.append("|---|---|---|---|")
        lines.append(f"| {project} | {window} | 已确认版,内容经本人复核 | 原始记录可应要求提供 |")
        lines.append("")
        for intro_line in str(payload.get("intro_note") or "").splitlines():
            lines.append(f"> {intro_line}")
    lines.append("")

    # ---- 事实层三节:variant 无关,两个版本逐字一致 ----
    lines.append("## 项目概述")
    lines.append("")
    overview = _field(payload, "overview")
    lines.append(_safe(overview.get("text", "")) + refs_for(list(overview.get("citations") or [])))
    lines.append("")

    lines.append("## 开发轨迹")
    lines.append("")
    if self_scoped:
        lines.append("阶段按**主线合入点**划分;仅含本人名下提交,协作方提交不在档案内。")
        lines.append("")
    lines.append("| 阶段 | 关键结果 |")
    lines.append("|---|---|")
    for stage in _list_field(payload, "stages"):
        texts: list[str] = []
        for change in _list_field(stage, "key_changes"):
            change_dict = _as_dict(change)
            if change_dict is not None:
                texts.append(_safe(change_dict.get("text", "")))
            else:
                texts.append(_safe(change))
        lines.append(
            f"| {_stage_number(stage)} · {_safe(stage.get('title', ''))} | {';'.join(texts)} |"
        )
    lines.append("")

    lines.append("## 阶段详情")
    for stage in _list_field(payload, "stages"):
        lines.append("")
        lines.append(f"### 阶段 {_stage_number(stage)} · {_safe(stage.get('title', ''))}")
        lines.append("")
        if stage.get("kind") == "inferred_truncated":
            lines.append(
                "> ⚠️ 本阶段的提交过程**未完整记录**(档案截断,仅从合并消息推断范围,不作过程断言)。"
            )
        else:
            lines.append(f"> 目标：{_safe(stage.get('goal', ''))}")
        lines.append("")
        lines.extend(_render_key_changes(_list_field(stage, "key_changes"), refs_for))
        stage_refs = refs_for(list(stage.get("citations") or []))
        if stage_refs:
            lines.append("")
            lines.append(f"本阶段关键结果证据：{stage_refs}")
        for gap in _list_field(stage, "evidence_gaps"):
            lines.append(f"- 证据缺口：{_safe(gap)}")
        git_events = _stage_git_events(stage, events_by_id)
        if git_events:
            lines.append("")
            lines.append(f"<details><summary>原始提交({len(git_events)} 条)</summary>")
            lines.append("")
            for event_id in git_events:
                event = events_by_id[event_id]
                lines.append(f"- `{event_id[8:15]}` {_safe(event.get('summary', ''))}")
            lines.append("</details>")
        lines.append("")

    # ---- 版本相关章节 ----
    diagram = payload.get("diagram")
    turning_points: list[NarrativeDict] = [
        tp
        for tp in (_as_dict(item) for item in _list_field(payload, "turning_points"))
        if tp is not None
    ]
    if selected == "working":
        lines.append("## 关键转折")
        lines.append("")
        if isinstance(diagram, str) and diagram:
            lines.append("```mermaid")
            lines.extend(diagram.splitlines())
            lines.append("```")
            lines.append("")
        shown = [tp for tp in turning_points if tp.get("status") in _CONFIRMED_OR_SUPPLEMENTED]
        if shown:
            for tp in shown:
                status_label = _TURNING_POINT_STATUS_LABELS[str(tp.get("status"))]
                lines.append(
                    f"- **{_safe(tp.get('title', ''))}** <sub>推断 · {status_label}</sub>:"
                    f"{_safe(tp.get('body', ''))}{refs_for(list(tp.get('citations') or []))}"
                )
            lines.append("")
            lines.append("> 系统推断需本人确认或补充;仅据此不能断言因果关系,以本人陈述为准。")
        else:
            lines.append("未记录;当前档案未产生经确认或补充的系统推断。")
        lines.append("")

        ai = _field(payload, "ai_collaboration")
        lines.append("## AI 协作")
        lines.append("")
        lines.append(f"- **可见性地图**:{_safe(ai.get('coverage', ''))}")
        lines.append(f"- **活动形状**:{_safe(ai.get('shape', ''))}")
        lines.append(f"- **文件焦点**:{_safe(ai.get('focus', ''))}")
        lines.append("- **关键插曲**：")
        episode_number = 0
        for episode in _list_field(ai, "episodes"):
            if episode.get("derived"):
                continue
            episode_number += 1
            lines.append(
                f"  {episode_number}. {_safe(episode.get('body', ''))}"
                f"{refs_for(list(episode.get('citations') or []))}"
            )
        for episode in _list_field(ai, "episodes"):
            if not episode.get("derived"):
                continue
            lines.append(
                f"- **派生摘要**(经完整对话授权后生成,可否认):"
                f"*{_safe(episode.get('label', ''))}*——{_safe(episode.get('body', ''))}"
                f"{refs_for(list(episode.get('citations') or []))}。"
                "<sub>派生 · 依据插曲内原子记录</sub>"
            )
        lines.append(f"- **边界声明**:{_safe(ai.get('boundary', ''))}")
        lines.append("")

        lines.extend(_render_verification_lines(payload))

        lines.append("## 学习收获")
        lines.append("")
        takeaways = _list_field(payload, "takeaways")
        if takeaways:
            for index, item in enumerate(takeaways, 1):
                lines.append(f"{index}. {_safe(item)}")
        else:
            lines.append("- 未记录;本节内容需由本人确认或填写,系统不代写。")
        denied_count = sum(
            1 for tp in turning_points if tp.get("status") == "denied_kept_in_appendix"
        )
        if denied_count:
            lines.append(f"- 有 {denied_count} 条系统线索已被本人否认,保留在附录备查。")
        lines.append("")

        lines.append("## 个人反思")
        lines.append("")
        if isinstance(reflection_text, str) and reflection_text:
            lines.append(_safe(reflection_text))
        else:
            lines.append("- 未记录;本节必须由本人填写。")
        lines.append("")
    else:
        lines.append("## 关键转折与我的处理")
        lines.append("")
        if isinstance(diagram, str) and diagram:
            lines.append("```mermaid")
            lines.extend(diagram.splitlines())
            lines.append("```")
            lines.append("")
        for tp in turning_points:
            lines.append(
                f"- **{_safe(tp.get('title', ''))}**:{_safe(tp.get('body', ''))}"
                f"{refs_for(list(tp.get('citations') or []))}"
            )
        lines.append("")

        lines.extend(_render_verification_lines(payload))

        lines.append("## AI 使用声明")
        lines.append("")
        lines.append(_safe(payload.get("ai_statement", "")))
        lines.append("")

        lines.append("## 学习收获与下一步")
        lines.append("")
        takeaways = _list_field(payload, "takeaways")
        if takeaways:
            for index, item in enumerate(takeaways, 1):
                lines.append(f"{index}. {_safe(item)}")
        else:
            lines.append("- 未记录")
        lines.append("")
        if isinstance(reflection_text, str) and reflection_text:
            lines.append("## 个人反思")
            lines.append("")
            lines.append(_safe(reflection_text))
            lines.append("")

    # ---- 证据边界(两版一致)----
    lines.append("## 证据边界")
    lines.append("")
    gaps = _list_field(payload, "evidence_gaps")
    if gaps:
        for index, gap in enumerate(gaps, 1):
            lines.append(f"> ⚠️ **证据缺口 {index}**：{_safe(gap)}")
    else:
        lines.append("- 未记录;当前档案未列出证据缺口。")
    lines.append("")

    # ---- 工作版附录(被否认线索 + 审计摘要,置于证据边界之后,与 golden 顺序一致)----
    if selected == "working":
        denied_turnings = [
            tp for tp in turning_points if tp.get("status") == "denied_kept_in_appendix"
        ]
        record_counts = _field(archive, "record_counts")
        lines.append("## 附录")
        lines.append("")
        if denied_turnings:
            lines.append(
                f"<details><summary>已否认线索({len(denied_turnings)} 条,"
                "仅备查,不作负面评价)</summary>"
            )
            lines.append("")
            for tp in denied_turnings:
                lines.append(
                    f"- 线索：**{_safe(tp.get('title', ''))}**——{_safe(tp.get('body', ''))}"
                    f"{refs_for(list(tp.get('citations') or []))}"
                )
            lines.append("- 处理：该线索不进入正文结论;相关事实保留在审计层。")
            lines.append("")
            lines.append("</details>")
            lines.append("")
        lines.append("<details><summary>审计摘要</summary>")
        lines.append("")
        lines.append(
            "- Schema：`v0`;候选生成模式:"
            f"`{_safe(archive.get('candidate_inference_mode', 'stub'))}`。"
        )
        lines.append(
            f"- 事实分层：observable_fact {record_counts.get('observable_fact', 0)} 条 / "
            f"candidate_inference {record_counts.get('candidate_inference', 0)} 条 / "
            f"student_confirmation {record_counts.get('student_confirmation', 0)} 条。"
        )
        lines.append("- 档案指纹与完整来源索引见 `.learntrace/archive-records.json`。")
        lines.append(f"- 解析告警：{len(_list_field(archive, 'warnings'))} 条。")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # ---- 脚注定义(内部事件 ID 只出现在这里与 原始提交 列表)----
    lines.append("---")
    lines.append("")
    for label in label_order:
        lines.append(_footnote_definition(label, label_map[label], events_by_id))

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "load_archive",
    "load_payload",
    "render_narrative_markdown",
    "verify_payload",
]
