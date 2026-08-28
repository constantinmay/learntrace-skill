"""叙事层 payload 的契约/红线/渲染测试(Issue #26,#59)。

Golden = 呈现契约:测试断言章节骨架、字段形状、引用解析、被否认线索隔离与
必需字段等结构不变量,不逐字比对 golden Markdown。
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from learntrace.cli import main as cli_main
from learntrace.models import ContractValidator
from learntrace.reporting import (
    load_archive,
    load_payload,
    render_narrative_markdown,
    verify_payload,
)

GOLDEN_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "report-golden"
SAMPLES = ("book-manager", "province-economy")
_INTERNAL_ID_RE = re.compile(r"evt-(?:git|doc|test|trace)-[a-f0-9]+")
_WORKING_SECTIONS = (
    "## 项目概述",
    "## 开发轨迹",
    "## 阶段详情",
    "## 关键转折",
    "## AI 协作",
    "## 验证与质量",
    "## 学习收获",
    "## 个人反思",
    "## 证据边界",
    "## 附录",
)
_SUBMITTED_SECTIONS = (
    "## 项目概述",
    "## 开发轨迹",
    "## 阶段详情",
    "## 关键转折与我的处理",
    "## 验证与质量",
    "## AI 使用声明",
    "## 学习收获与下一步",
    "## 证据边界",
)
_DENIED_BASIS_EVENT = "evt-git-3eac32114182bb9bca32ce8f13527d8ed04e45ae"


def _event_kind(event_id: str) -> str:
    for prefix, kind in (
        ("evt-git-", "git_commit"),
        ("evt-doc-", "document"),
        ("evt-test-", "test_log"),
        ("evt-trace-", "trace_record"),
    ):
        if event_id.startswith(prefix):
            return kind
    msg = f"unknown event id: {event_id}"
    raise ValueError(msg)


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


def _as_list(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return cast("list[Any]", value)
    return None


def _citation_event_id(citation: Any) -> str:
    if isinstance(citation, str):
        return citation
    mapping = _as_dict(citation)
    assert mapping is not None and "event_id" in mapping
    return str(mapping["event_id"])


def _cited_event_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for citation in payload["overview"]["citations"]:
        ids.append(_citation_event_id(citation))
    for stage in payload["stages"]:
        for citation in stage["citations"]:
            ids.append(_citation_event_id(citation))
        for change in stage["key_changes"]:
            change_dict = _as_dict(change)
            if change_dict is not None:
                for citation in change_dict["citations"]:
                    ids.append(_citation_event_id(citation))
        anchor = stage.get("merge_anchor")
        if isinstance(anchor, str):
            ids.append(anchor)
        else:
            anchor_list = _as_list(anchor)
            if anchor_list is not None:
                ids.extend(anchor_list)
    for turning in payload["turning_points"]:
        for citation in turning["citations"]:
            ids.append(_citation_event_id(citation))
    for episode in payload["ai_collaboration"]["episodes"]:
        for citation in episode["citations"]:
            ids.append(_citation_event_id(citation))
    return list(dict.fromkeys(ids))


def _synthesize_archive(payload: dict[str, Any]) -> dict[str, Any]:
    """从 payload 引用合成最小档案(事件全集 + 被否认候选),用于红线与渲染测试。"""
    events = [
        {
            "id": event_id,
            "kind": _event_kind(event_id),
            "summary": f"事件摘要 {event_id[8:15]}",
            "source_refs": [{"type": _event_kind(event_id), "ref": f"fixture:{index}"}],
        }
        for index, event_id in enumerate(_cited_event_ids(payload))
    ]
    return {
        "learntrace_bundle": True,
        "candidate_inference_mode": "stub",
        "record_counts": {
            "observable_fact": len(events),
            "candidate_inference": 1,
            "student_confirmation": 1,
            "missing_info": 0,
            "warnings": 0,
            "pending_questions": payload["meta"]["pending_questions"],
        },
        "events": events,
        "candidates": [
            {
                "id": "cand-git-fix_failed_approach-44824fe24bb165a7",
                "node_type": "fix_failed_approach",
                "statement": "提交记录表明可能修复了一个实现问题",
                "basis_event_ids": [_DENIED_BASIS_EVENT],
                "uncertainty": "medium",
                "question_to_student": "修复前你如何发现这个问题?",
                "status": "resolved",
            }
        ],
        "confirmations": [
            {
                "id": "conf-04497032c6d8d437",
                "candidate_id": "cand-git-fix_failed_approach-44824fe24bb165a7",
                "decision": "denied",
                "student_statement": "不知道,AI自己修的",
            }
        ],
        "warnings": [],
        "pending_questions": [
            {"candidate_id": f"pending-{index}", "node_type": "follow_up"}
            for index in range(payload["meta"]["pending_questions"])
        ],
    }


def _fact_layer(markdown: str) -> str:
    start = markdown.index("## 项目概述")
    end = markdown.index("## 关键转折")
    return markdown[start:end]


def _section(markdown: str, start_header: str, end_header: str) -> str:
    start = markdown.index(start_header)
    end = markdown.index(end_header, start)
    return markdown[start:end]


@pytest.fixture(scope="module", params=SAMPLES)
def golden_sample(request: pytest.FixtureRequest) -> tuple[str, dict[str, Any], dict[str, Any]]:
    name = str(request.param)
    payload = load_payload(GOLDEN_DIR / name / "narrative-payload.golden.json")
    return name, payload, _synthesize_archive(payload)


def test_golden_payloads_pass_schema(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, _ = golden_sample
    validator = ContractValidator()
    assert validator.iter_errors("narrative_payload", payload) == []


def test_golden_payloads_pass_all_three_red_lines(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    assert verify_payload(payload, archive) == []


def test_render_is_deterministic(golden_sample: tuple[str, dict[str, Any], dict[str, Any]]) -> None:
    _, payload, archive = golden_sample
    first = render_narrative_markdown(payload, archive, variant="working")
    second = render_narrative_markdown(payload, archive, variant="working")
    assert first == second


def test_working_variant_section_structure(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    lines = render_narrative_markdown(payload, archive, variant="working").splitlines()
    positions = [lines.index(section) for section in _WORKING_SECTIONS]
    assert positions == sorted(positions)
    assert lines[0].startswith("# ") and "未确认版" in lines[0]
    assert lines[2] == "| 项目 | 证据窗口 | 版本状态 | 复盘提示 | 证据缺口 |"


def test_submitted_variant_section_structure(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    lines = render_narrative_markdown(payload, archive, variant="submitted").splitlines()
    positions = [lines.index(section) for section in _SUBMITTED_SECTIONS]
    assert positions == sorted(positions)
    assert lines[0] == f"# {payload['meta']['project']} — 项目复盘"
    assert lines[2] == "| 项目 | 日期 | 版本状态 | 证据可核查 |"
    assert "## AI 协作" not in lines
    assert "## 附录" not in lines


def test_fact_layer_three_sections_are_verbatim_identical(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    working = render_narrative_markdown(payload, archive, variant="working")
    submitted = render_narrative_markdown(payload, archive, variant="submitted")
    assert _fact_layer(working) == _fact_layer(submitted)


def test_internal_event_ids_only_in_footnote_definitions(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    markdown = render_narrative_markdown(payload, archive, variant="working")
    lines = markdown.splitlines()
    body = "\n".join(line for line in lines if not line.startswith("[^"))
    assert not _INTERNAL_ID_RE.search(body)
    definitions = "\n".join(line for line in lines if line.startswith("[^"))
    for event_id in _cited_event_ids(payload):
        assert event_id in definitions


def test_denied_clues_stay_out_of_body(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    denied = [
        turning
        for turning in payload["turning_points"]
        if turning["status"] == "denied_kept_in_appendix"
    ]
    if not denied:
        pytest.skip("该样例没有 denied 转折")
    markdown = render_narrative_markdown(payload, archive, variant="working")
    appendix_position = markdown.index("## 附录")
    body = markdown[:appendix_position]
    for turning in denied:
        assert turning["title"] not in body
    appendix = markdown[appendix_position:]
    for turning in denied:
        assert turning["title"] in appendix
    assert "仅备查,不作负面评价" in appendix
    assert "## 附录" in markdown and markdown.index("## 证据边界") < appendix_position


def test_derived_episodes_are_marked(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    markdown = render_narrative_markdown(payload, archive, variant="working")
    ai_section = _section(markdown, "## AI 协作", "## 验证与质量")
    elements = ("**可见性地图**", "**活动形状**", "**文件焦点**", "**关键插曲**", "**边界声明**")
    for element in elements:
        assert element in ai_section
    for episode in payload["ai_collaboration"]["episodes"]:
        if episode["derived"]:
            assert "派生摘要" in ai_section and "可否认" in ai_section
            assert "<sub>派生" in ai_section


def test_truncated_stage_omits_goal_line() -> None:
    name = "province-economy"
    payload = load_payload(GOLDEN_DIR / name / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    markdown = render_narrative_markdown(payload, archive, variant="working")
    stage0 = _section(markdown, "### 阶段 0", "### 阶段 1")
    assert "未完整记录" in stage0
    assert "不作过程断言" in stage0
    assert "> 目标" not in stage0
    plain = _section(markdown, "### 阶段 1", "### 阶段 2")
    assert "> 目标" in plain


def test_kind_grouped_key_changes_render() -> None:
    payload = load_payload(GOLDEN_DIR / "book-manager" / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    first_citation = payload["stages"][0]["citations"][0]
    payload["stages"][0]["key_changes"] = [
        {"kind": "Added", "text": "仓库骨架", "citations": [first_citation]},
        {"kind": "Removed", "text": "编译产物移出"},
        {"kind": "Collaboration", "text": "无协作内容"},
    ]
    assert verify_payload(payload, archive) == []
    markdown = render_narrative_markdown(payload, archive, variant="working")
    stage1 = _section(markdown, "### 阶段 1", "### 阶段 2")
    for heading in ("**Added**", "**Removed**", "**协作边界**"):
        assert heading in stage1
    assert "> 目标" in stage1


def test_dev_trajectory_table_uses_text_only() -> None:
    payload = load_payload(GOLDEN_DIR / "book-manager" / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    payload["stages"][0]["key_changes"] = [
        {"kind": "Added", "text": "仓库骨架", "citations": []},
        {"kind": "Fixed", "text": "统一时间格式", "citations": []},
    ]
    markdown = render_narrative_markdown(payload, archive, variant="working")
    trajectory = _section(markdown, "## 开发轨迹", "## 阶段详情")
    assert "| 1 · 初始化与数据层 | 仓库骨架;统一时间格式 |" in trajectory


def test_redaction_applied_at_render_boundary() -> None:
    payload = load_payload(GOLDEN_DIR / "book-manager" / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    payload["overview"]["text"] = (
        "课程项目说明。配置 token=supersecret123,本地在 ~/projects/book 下开发,"
        "仓库位于 D:/code/book 目录。"
    )
    markdown = render_narrative_markdown(payload, archive, variant="working")
    assert "supersecret123" not in markdown
    assert "[REDACTED]" in markdown
    assert "~/projects/book" not in markdown
    assert "[private-path]" in markdown
    assert "[absolute-path]" in markdown


def test_citation_with_label_and_evidence_location() -> None:
    payload = load_payload(GOLDEN_DIR / "book-manager" / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    event_id = payload["stages"][0]["citations"][0]
    payload["stages"][0]["citations"] = [
        {
            "event_id": event_id,
            "label": "c-skeleton",
            "evidence_location": {"kind": "git_commit", "hash": event_id[8:]},
        }
    ]
    assert verify_payload(payload, archive) == []
    markdown = render_narrative_markdown(payload, archive, variant="working")
    assert "[^c-skeleton]" in markdown
    assert "[^c-skeleton]: " in markdown
    assert event_id in markdown  # 仅脚注定义中
    body = "\n".join(line for line in markdown.splitlines() if not line.startswith("[^"))
    assert event_id not in body


def test_schema_rejects_unknown_evidence_location_kind() -> None:
    payload = load_payload(GOLDEN_DIR / "book-manager" / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    event_id = payload["stages"][0]["citations"][0]
    payload["stages"][0]["citations"] = [
        {
            "event_id": event_id,
            "evidence_location": {"kind": "unknown_kind", "hash": "abcdef1"},
        }
    ]
    violations = verify_payload(payload, archive)
    assert any(violation.startswith("schema:") for violation in violations)


def test_red_line_1_unresolvable_citation(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    bad = copy.deepcopy(payload)
    bad["overview"]["citations"] = ["evt-doc-0000000000000000"]
    violations = verify_payload(bad, archive)
    assert any("红线1" in violation for violation in violations)


def test_malformed_citation_is_rejected(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    """缺 event_id 的引用对象先被 schema 拦下(verify 提前返回 schema 错误)。"""
    _, payload, _ = golden_sample
    bad = copy.deepcopy(payload)
    bad["overview"]["citations"] = [{"label": "missing-event-id"}]
    violations = verify_payload(bad, {})
    assert violations
    assert all(violation.startswith("schema:") for violation in violations)


@pytest.mark.parametrize("status", ["confirmed", "supplemented"])
def test_red_line_2_denied_basis_cited_by_body_turning_point(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]], status: str
) -> None:
    _, payload, archive = golden_sample
    bad = copy.deepcopy(payload)
    bad["turning_points"].append(
        {
            "title": "坏转折",
            "status": status,
            "body": "引用了被本人否认线索的 basis 事件",
            "citations": [_DENIED_BASIS_EVENT],
        }
    )
    violations = verify_payload(bad, archive)
    assert any("红线2" in violation for violation in violations)


def test_red_line_2_denied_slot_not_allowed_in_submitted() -> None:
    payload = load_payload(GOLDEN_DIR / "book-manager" / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    bad = copy.deepcopy(payload)
    bad["variant"] = "submitted"
    bad["intro_note"] = "这是我课程大作业的过程复盘。"
    bad["ai_statement"] = "本项目使用了 AI 编程助手,全部经本人复核。"
    bad["takeaways"] = ["收获一"]
    bad["reflection"]["text"] = "本人反思。"
    bad["turning_points"] = [
        {
            "title": "修复 created_at 格式不一致",
            "status": "denied_kept_in_appendix",
            "body": "系统推断已被本人否认",
            "citations": [],
        }
    ]
    violations = verify_payload(bad, archive)
    assert any("红线2" in violation for violation in violations)


def test_red_line_3_meta_count_mismatch(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    bad = copy.deepcopy(payload)
    bad["meta"]["evidence_gaps"] = 99
    bad["meta"]["pending_questions"] = 99
    violations = verify_payload(bad, archive)
    assert sum("红线3" in violation for violation in violations) >= 2


def test_red_line_3_working_reflection_must_be_null(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, archive = golden_sample
    bad = copy.deepcopy(payload)
    bad["reflection"]["text"] = "AI 代写的反思"
    violations = verify_payload(bad, archive)
    assert any("红线3" in violation and "reflection" in violation for violation in violations)


def test_red_line_3_submitted_requires_student_content() -> None:
    payload = load_payload(GOLDEN_DIR / "book-manager" / "narrative-payload.golden.json")
    archive = _synthesize_archive(payload)
    bad = copy.deepcopy(payload)
    bad["variant"] = "submitted"
    bad["turning_points"] = []
    violations = verify_payload(bad, archive)
    joined = "\n".join(violations)
    for fragment in ("intro_note", "ai_statement", "takeaways", "reflection.text"):
        assert fragment in joined


def test_schema_rejects_missing_required_field(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, _ = golden_sample
    bad = copy.deepcopy(payload)
    del bad["verification"]
    violations = verify_payload(bad, {})
    assert any(violation.startswith("schema:") for violation in violations)


def test_schema_rejects_derived_episode_without_deniable(
    golden_sample: tuple[str, dict[str, Any], dict[str, Any]],
) -> None:
    _, payload, _ = golden_sample
    bad = copy.deepcopy(payload)
    episode = bad["ai_collaboration"]["episodes"][0]
    episode["derived"] = True
    violations = verify_payload(bad, {})
    assert any(violation.startswith("schema:") for violation in violations)


def _write_pair(
    tmp_path: Path,
    payload: dict[str, Any],
    archive: dict[str, Any],
) -> tuple[Path, Path]:
    payload_path = tmp_path / "payload.json"
    archive_path = tmp_path / "archive-records.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    archive_path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
    return payload_path, archive_path


def test_cli_verify_narrative_pass_and_fail(
    tmp_path: Path, golden_sample: tuple[str, dict[str, Any], dict[str, Any]]
) -> None:
    _, payload, archive = golden_sample
    payload_path, archive_path = _write_pair(tmp_path, payload, archive)
    assert cli_main(["verify-narrative", str(payload_path), str(archive_path)]) == 0
    bad = copy.deepcopy(payload)
    bad["overview"]["citations"] = ["evt-doc-deadbeefdeadbeef"]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    assert cli_main(["verify-narrative", str(bad_path), str(archive_path)]) == 1


def test_cli_render_narrative_writes_output(
    tmp_path: Path, golden_sample: tuple[str, dict[str, Any], dict[str, Any]]
) -> None:
    _, payload, archive = golden_sample
    payload_path, archive_path = _write_pair(tmp_path, payload, archive)
    output_path = tmp_path / "out" / "record.md"
    assert (
        cli_main(["render-narrative", str(payload_path), str(archive_path), "-o", str(output_path)])
        == 0
    )
    assert output_path.read_text(encoding="utf-8") == render_narrative_markdown(
        payload, archive, variant="working"
    )


def test_cli_render_narrative_rejects_unverified_payload(
    tmp_path: Path, golden_sample: tuple[str, dict[str, Any], dict[str, Any]]
) -> None:
    _, payload, archive = golden_sample
    bad = copy.deepcopy(payload)
    bad["meta"]["pending_questions"] = 42
    payload_path, archive_path = _write_pair(tmp_path, bad, archive)
    output_path = tmp_path / "should-not-exist.md"
    assert (
        cli_main(["render-narrative", str(payload_path), str(archive_path), "-o", str(output_path)])
        == 1
    )
    assert not output_path.exists()


def test_loaders_reject_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON 对象"):
        load_payload(path)
    with pytest.raises(ValueError, match="JSON 对象"):
        load_archive(path)
