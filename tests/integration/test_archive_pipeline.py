from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from learntrace.archive import load_project_artifacts, load_project_records, write_learning_record
from learntrace.models import (
    ConfirmationDecision,
    ContractValidator,
    EventKind,
    MissingInfo,
    NodeType,
    ObservableEvent,
    SourceRef,
    SourceType,
    StudentConfirmation,
)
from learntrace.reporting import (
    ArchiveWarning,
    CandidateDraft,
    build_archive_bundle,
    bundle_to_dict,
    render_markdown,
)
from learntrace.reporting.pipeline import MAX_CANDIDATES, StubCandidateInferencer

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "src" / "learntrace" / "schemas" / "v0"
SCENARIOS_DIR = REPO_ROOT / "tests" / "fixtures" / "golden" / "scenarios"
SCENARIO_DIRS = tuple(sorted(path for path in SCENARIOS_DIR.iterdir() if path.is_dir()))


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_candidates(scenario_dir: Path) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], _load_json(path))
        for path in sorted(scenario_dir.glob("learning-node-candidate*.json"))
    ]


def _expected_confirmations(scenario_dir: Path) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], _load_json(path))
        for path in sorted(scenario_dir.glob("student-confirmation*.json"))
    ]


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=lambda path: path.name)
def test_archive_bundle_matches_golden_scenarios(scenario_dir: Path) -> None:
    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    events, confirmations = load_project_records(scenario_dir, validator=validator)

    bundle = build_archive_bundle(events, confirmations=confirmations, validator=validator)

    actual_candidates = [candidate.to_dict() for candidate in bundle.candidates]
    expected_candidates = _expected_candidates(scenario_dir)
    assert actual_candidates == expected_candidates

    actual_confirmations = [confirmation.to_dict() for confirmation in bundle.confirmations]
    expected_confirmations = _expected_confirmations(scenario_dir)
    assert actual_confirmations == expected_confirmations


@pytest.mark.parametrize("scenario_dir", SCENARIO_DIRS, ids=lambda path: path.name)
def test_render_markdown_contains_required_sections(scenario_dir: Path) -> None:
    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    events, confirmations = load_project_records(scenario_dir, validator=validator)
    bundle = build_archive_bundle(events, confirmations=confirmations, validator=validator)
    markdown = render_markdown(bundle, source_dir=scenario_dir)

    for heading in (
        "## 项目概览",
        "## 项目目标",
        "## 审计摘要",
        "## 证据分层",
        "## AI 使用",
        "## 关键决策",
        "## 验证证据",
        "## 个人反思",
        "## 后续学习",
        "## AI 使用声明",
    ):
        assert heading in markdown


def test_render_markdown_does_not_expose_source_directory(tmp_path: Path) -> None:
    event = ObservableEvent(
        id="evt-source-dir-privacy",
        kind=EventKind.DOCUMENT,
        summary="记录了实现目标。",
        source_refs=(SourceRef(type=SourceType.DOCUMENT, ref="task.md:1-2"),),
    )
    bundle = build_archive_bundle((event,))

    markdown = render_markdown(bundle, source_dir=tmp_path.resolve())

    assert str(tmp_path.resolve()) not in markdown
    assert "- 证据目录：." in markdown
    assert "~/.ssh" not in render_markdown(bundle, source_dir=Path("~/.ssh"))


def test_render_markdown_redacts_paths_and_secrets_from_record_body(tmp_path: Path) -> None:
    private_root = str(tmp_path.resolve())
    secret = "sk-abcdefgh12345678"
    event = ObservableEvent(
        id="evt-body-privacy",
        kind=EventKind.DOCUMENT,
        summary=f"项目目标记录在 {private_root}\\notes.md，token={secret}",
        source_refs=(SourceRef(type=SourceType.DOCUMENT, ref=f"{private_root}\\notes.md:1-2"),),
    )
    bundle = build_archive_bundle(
        (event,),
        warnings=(
            ArchiveWarning(
                code="private-warning",
                source=f"{private_root}\\input.json",
                message="用户目录 ~/.ssh/id_rsa 无法读取",
            ),
        ),
    )

    markdown = render_markdown(bundle, source_dir=tmp_path.resolve())

    assert private_root not in markdown
    assert "~/.ssh/id_rsa" not in markdown
    assert secret not in markdown
    assert "[REDACTED]" in markdown
    assert "[absolute-path]" in markdown
    assert "[private-path]" in markdown


def test_render_markdown_handles_missing_info_and_degraded_cases() -> None:
    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    scenario_04 = SCENARIOS_DIR / "04-fix-failed-approach-missing-evidence"
    scenario_08 = SCENARIOS_DIR / "08-no-trace-degraded"
    scenario_09 = SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty"

    events_04, confirmations_04 = load_project_records(
        scenario_04,
        validator=validator,
    )
    bundle_04 = build_archive_bundle(events_04, confirmations=confirmations_04, validator=validator)
    markdown_04 = render_markdown(bundle_04, source_dir=scenario_04)
    assert "未记录" in markdown_04

    events_08, confirmations_08 = load_project_records(
        scenario_08,
        validator=validator,
    )
    bundle_08 = build_archive_bundle(events_08, confirmations=confirmations_08, validator=validator)
    markdown_08 = render_markdown(bundle_08, source_dir=scenario_08)
    assert "当前证据未形成可提问的学习节点候选。" in markdown_08
    assert "未见与本项目范围相关的授权轨迹" in markdown_08

    events_09, confirmations_09 = load_project_records(
        scenario_09,
        validator=validator,
    )
    bundle_09 = build_archive_bundle(events_09, confirmations=confirmations_09, validator=validator)
    markdown_09 = render_markdown(bundle_09, source_dir=scenario_09)
    assert (
        "待补充 cand-s09-adjust_constraints-5678a64e29b362f5：调整缺失输入的处理方式是出于什么考虑？"  # noqa: E501
        in markdown_09
    )


def test_render_markdown_extracts_documented_goal_without_inventing_one() -> None:
    goal_event = ObservableEvent(
        id="evt-goal",
        kind=EventKind.DOCUMENT,
        summary="README 记录项目目标：实现一个本地课程学习档案生成器。",
        source_refs=(SourceRef(type=SourceType.DOCUMENT, ref="README.md:1-4"),),
    )
    commit_event = ObservableEvent(
        id="evt-commit",
        kind=EventKind.GIT_COMMIT,
        summary="提交 a1b2c3d：初始化项目。",
        source_refs=(SourceRef(type=SourceType.GIT_COMMIT, ref="a1b2c3d"),),
    )

    with_goal = render_markdown(build_archive_bundle((goal_event,)))
    without_goal = render_markdown(build_archive_bundle((commit_event,)))

    assert "## 项目目标" in with_goal
    assert "实现一个本地课程学习档案生成器" in with_goal
    assert "请由学生根据课程任务或项目 README 补充" in without_goal


def test_ai_use_section_filters_irrelevant_trace_events() -> None:
    traces = (
        ObservableEvent(
            id="evt-meaningful",
            kind=EventKind.TRACE_RECORD,
            summary="学生追问为何要为解析器补充边界测试。",
            source_refs=(SourceRef(type=SourceType.TRACE_RECORD, ref="trace://session/1"),),
        ),
        ObservableEvent(
            id="evt-outside",
            kind=EventKind.TRACE_RECORD,
            summary="OpenCode 工具 write 已完成。路径：[outside-project]。",
            source_refs=(SourceRef(type=SourceType.TRACE_RECORD, ref="trace://session/2"),),
        ),
        ObservableEvent(
            id="evt-kill",
            kind=EventKind.TRACE_RECORD,
            summary="OpenCode 工具 bash 已完成。命令类型：kill。",
            source_refs=(SourceRef(type=SourceType.TRACE_RECORD, ref="trace://session/3"),),
        ),
        ObservableEvent(
            id="evt-self",
            kind=EventKind.TRACE_RECORD,
            summary="OpenCode 工具 edit 已完成。路径：skills/learntrace/SKILL.md。",
            source_refs=(SourceRef(type=SourceType.TRACE_RECORD, ref="trace://session/4"),),
        ),
        ObservableEvent(
            id="evt-generic",
            kind=EventKind.TRACE_RECORD,
            summary="OpenCode 工具 write 已完成。路径：src/app.py。",
            source_refs=(SourceRef(type=SourceType.TRACE_RECORD, ref="trace://session/5"),),
        ),
    )

    markdown = render_markdown(build_archive_bundle(traces))
    ai_section = markdown.split("## AI 使用\n", 1)[1].split("\n## 关键决策", 1)[0]

    assert "evt-meaningful" in ai_section
    for hidden_id in ("evt-outside", "evt-kill", "evt-self", "evt-generic"):
        assert hidden_id not in ai_section
    assert "已过滤或省略 4 条" in ai_section


def test_reflection_is_an_editable_student_field_not_confirmation_echo() -> None:
    scenario = SCENARIOS_DIR / "01-revise-ai-suggestion-confirmed"
    events, confirmations = load_project_records(scenario)
    bundle = build_archive_bundle(events, confirmations=confirmations)

    markdown = render_markdown(bundle)
    reflection = markdown.split("## 个人反思\n", 1)[1].split("\n## 后续学习", 1)[0]

    assert "系统不会用确认陈述代写反思" in reflection
    assert "默认推断会把含千分位的列解析成字符串" not in reflection


def test_cli_writes_learning_record(tmp_path: Path) -> None:
    output_path = tmp_path / "learning-record.md"
    write_learning_record(
        SCENARIOS_DIR / "01-revise-ai-suggestion-confirmed",
        output_path=output_path,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    assert output_path.exists()
    markdown = output_path.read_text(encoding="utf-8")
    assert "学生可能没有直接采用 AI 的数据解析建议" in markdown


def test_loads_task2_style_batch_json(tmp_path: Path) -> None:
    scenario_dir = SCENARIOS_DIR / "05-add-tests-confirmed"
    event_records = [
        _load_json(scenario_dir / "observable-event-commit.json"),
        _load_json(scenario_dir / "observable-event-testlog.json"),
    ]
    confirmation_records = [
        _load_json(scenario_dir / "student-confirmation.json"),
    ]
    batch = {
        "learntrace_bundle": True,
        "parser_version": "v0",
        "events": event_records,
        "confirmations": confirmation_records,
        "warnings": [
            {
                "code": "unsupported_test_log_format",
                "source": "logs/unknown.log",
                "message": "格式未识别，保留为边界告警。",
            }
        ],
    }
    (tmp_path / "parse-result.json").write_text(
        json.dumps(batch, ensure_ascii=False),
        encoding="utf-8",
    )

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    loaded = load_project_artifacts(tmp_path, validator=validator)
    bundle = build_archive_bundle(
        loaded.events,
        confirmations=loaded.confirmations,
        warnings=loaded.warnings,
        validator=validator,
    )

    assert [candidate.to_dict() for candidate in bundle.candidates] == _expected_candidates(
        scenario_dir
    )
    assert [confirmation.to_dict() for confirmation in bundle.confirmations] == confirmation_records
    markdown = render_markdown(bundle, source_dir=tmp_path)
    assert "unsupported_test_log_format [logs/unknown.log]" in markdown


def test_realistic_task2_task3_chain_avoids_candidate_explosion(tmp_path: Path) -> None:
    base_time = datetime(2026, 8, 10, 9, 30, tzinfo=UTC)
    documents = tuple(
        ObservableEvent(
            id=f"evt-real-doc-{index:02d}",
            kind=EventKind.DOCUMENT,
            summary=f"文档章节 {index} 记录项目功能说明和运行步骤。",
            source_refs=(SourceRef(type=SourceType.DOCUMENT, ref=f"README.md:{index * 5 + 1}"),),
        )
        for index in range(13)
    )
    commits = tuple(
        ObservableEvent(
            id=f"evt-real-commit-{index:02d}",
            kind=EventKind.GIT_COMMIT,
            summary=f"提交 a1b2c{index:x}：完成 module_{index} 功能实现。",
            source_refs=(
                SourceRef(type=SourceType.GIT_COMMIT, ref=f"a1b2c{index:x}"),
                SourceRef(type=SourceType.FILE, ref=f"src/module_{index}.py"),
            ),
            occurred_at=(base_time + timedelta(minutes=(index + 1) * 3)).isoformat(),
        )
        for index in range(12)
    )
    traces = tuple(
        ObservableEvent(
            id=f"evt-real-trace-{index:03d}",
            kind=EventKind.TRACE_RECORD,
            summary=(f"OpenCode 工具 write 已完成。路径：src/module_{min(index // 10, 11)}.py。"),
            source_refs=(SourceRef(type=SourceType.TRACE_RECORD, ref=f"trace://opencode/{index}"),),
            occurred_at=(base_time + timedelta(seconds=index * 20)).isoformat(),
        )
        for index in range(125)
    )
    batch = {
        "learntrace_bundle": True,
        "parser_version": "v0",
        "events": [event.to_dict() for event in (*documents, *commits, *traces)],
        "warnings": [],
    }
    (tmp_path / "merged-result.json").write_text(
        json.dumps(batch, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_project_artifacts(tmp_path, validator=ContractValidator(schema_dir=SCHEMA_DIR))
    bundle = build_archive_bundle(
        loaded.events,
        inferencer=StubCandidateInferencer(),
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    assert 0 < len(bundle.candidates) <= MAX_CANDIDATES
    assert all(
        candidate.node_type != NodeType.ADJUST_CONSTRAINTS for candidate in bundle.candidates
    )
    event_kind_by_id = {event.id: event.kind for event in bundle.events}
    assert all(
        any(
            event_kind_by_id[event_id] == EventKind.TRACE_RECORD
            for event_id in candidate.basis_event_ids
        )
        for candidate in bundle.candidates
    )


def test_loads_task2_style_batch_json_without_marker_when_events_look_valid(tmp_path: Path) -> None:
    scenario_dir = SCENARIOS_DIR / "05-add-tests-confirmed"
    batch = {
        "parser_version": "v0",
        "events": [
            _load_json(scenario_dir / "observable-event-commit.json"),
            _load_json(scenario_dir / "observable-event-testlog.json"),
        ],
        "confirmations": [
            _load_json(scenario_dir / "student-confirmation.json"),
        ],
    }
    (tmp_path / "parse-result.json").write_text(
        json.dumps(batch, ensure_ascii=False),
        encoding="utf-8",
    )

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    loaded = load_project_artifacts(tmp_path, validator=validator)
    bundle = build_archive_bundle(
        loaded.events,
        confirmations=loaded.confirmations,
        warnings=loaded.warnings,
        validator=validator,
    )

    assert [candidate.to_dict() for candidate in bundle.candidates] == _expected_candidates(
        scenario_dir
    )


def test_cli_writes_machine_readable_archive_and_questions(tmp_path: Path) -> None:
    output_path = tmp_path / "learning-record.md"
    records_output = tmp_path / "archive-records.json"
    questions_output = tmp_path / "learning-questions.md"
    write_learning_record(
        SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty",
        output_path=output_path,
        records_output_path=records_output,
        questions_output_path=questions_output,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    records = cast(dict[str, Any], _load_json(records_output))
    record_counts = cast(dict[str, int], records["record_counts"])
    pending_questions = cast(list[dict[str, Any]], records["pending_questions"])
    assert records["archive_version"] == "v0"
    assert record_counts["pending_questions"] == 1
    assert pending_questions[0]["candidate_id"] == "cand-s09-adjust_constraints-5678a64e29b362f5"
    quality_checks = cast(dict[str, bool], records["quality_checks"])
    risk_flags = cast(dict[str, bool], records["risk_flags"])
    assert quality_checks["schema_valid"] is True
    assert risk_flags["has_pending_questions"] is True
    assert "cand-s09-adjust_constraints-5678a64e29b362f5" in questions_output.read_text(
        encoding="utf-8"
    )


def test_machine_readable_archive_includes_stable_manifest(tmp_path: Path) -> None:
    first_records_output = tmp_path / "archive-records-first.json"
    second_records_output = tmp_path / "nested" / "archive-records-second.json"
    scenario_dir = SCENARIOS_DIR / "05-add-tests-confirmed"
    validator = ContractValidator(schema_dir=SCHEMA_DIR)

    write_learning_record(
        scenario_dir,
        output_path=tmp_path / "learning-record-first.md",
        records_output_path=first_records_output,
        validator=validator,
    )
    write_learning_record(
        scenario_dir,
        output_path=tmp_path / "nested" / "learning-record-second.md",
        records_output_path=second_records_output,
        validator=validator,
    )

    first_records = cast(dict[str, Any], _load_json(first_records_output))
    second_records = cast(dict[str, Any], _load_json(second_records_output))
    first_manifest = cast(dict[str, Any], first_records["archive_manifest"])
    second_manifest = cast(dict[str, Any], second_records["archive_manifest"])

    assert first_manifest["hash_algorithm"] == "sha256"
    assert first_manifest["content_fingerprint"] == second_manifest["content_fingerprint"]
    assert len(cast(str, first_manifest["content_fingerprint"])) == 64
    record_hashes = cast(dict[str, str], first_manifest["record_hashes"])
    assert set(record_hashes) == {
        "observable_fact",
        "candidate_inference",
        "student_confirmation",
        "warnings",
    }


def test_machine_readable_archive_includes_provenance_indexes(tmp_path: Path) -> None:
    records_output = tmp_path / "archive-records.json"
    write_learning_record(
        SCENARIOS_DIR / "05-add-tests-confirmed",
        output_path=tmp_path / "learning-record.md",
        records_output_path=records_output,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    records = cast(dict[str, Any], _load_json(records_output))
    source_index = cast(list[dict[str, Any]], records["source_index"])
    candidate_links = cast(list[dict[str, Any]], records["candidate_links"])

    git_source = next(
        entry
        for entry in source_index
        if cast(dict[str, str], entry["source_ref"])["ref"] == "f6a7b8c"
    )
    assert git_source["event_ids"] == ["evt-s05-1"]
    assert git_source["candidate_ids"] == ["cand-s05-add_tests-30682dc7fed30c52"]
    assert candidate_links == [
        {
            "candidate_id": "cand-s05-add_tests-30682dc7fed30c52",
            "node_type": "add_tests",
            "basis_event_ids": ["evt-s05-1", "evt-s05-2"],
            "confirmation_id": "conf-s05",
            "status": "resolved",
        }
    ]


def test_machine_readable_archive_reports_stub_inference_mode(tmp_path: Path) -> None:
    records_output = tmp_path / "archive-records.json"
    write_learning_record(
        SCENARIOS_DIR / "05-add-tests-confirmed",
        output_path=tmp_path / "learning-record.md",
        records_output_path=records_output,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    records = cast(dict[str, Any], _load_json(records_output))
    assert records["candidate_inference_mode"] == "stub"


def test_output_files_are_overwritten_atomically(tmp_path: Path) -> None:
    records_output = tmp_path / "archive-records.json"
    questions_output = tmp_path / "learning-questions.md"
    records_output.write_text("old records", encoding="utf-8")
    questions_output.write_text("old questions", encoding="utf-8")

    write_learning_record(
        SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty",
        output_path=tmp_path / "learning-record.md",
        records_output_path=records_output,
        questions_output_path=questions_output,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    assert cast(dict[str, Any], _load_json(records_output))["archive_version"] == "v0"
    assert "old questions" not in questions_output.read_text(encoding="utf-8")


def test_output_paths_must_be_distinct(tmp_path: Path) -> None:
    output_path = tmp_path / "same-output"

    with pytest.raises(ValueError, match="output path conflicts"):
        write_learning_record(
            SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty",
            output_path=output_path,
            records_output_path=output_path,
            validator=ContractValidator(schema_dir=SCHEMA_DIR),
        )


def test_archive_json_can_be_reloaded_without_duplicate_record_failures(tmp_path: Path) -> None:
    scenario_dir = SCENARIOS_DIR / "05-add-tests-confirmed"
    records_output = tmp_path / "archive-records.json"
    write_learning_record(
        scenario_dir,
        output_path=tmp_path / "learning-record.md",
        records_output_path=records_output,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    for source in scenario_dir.glob("*.json"):
        (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    loaded = load_project_artifacts(tmp_path, validator=validator)
    bundle = build_archive_bundle(
        loaded.events,
        confirmations=loaded.confirmations,
        warnings=loaded.warnings,
        validator=validator,
    )

    records = bundle_to_dict(bundle, validator=validator)
    record_counts = cast(dict[str, int], records["record_counts"])
    assert record_counts["observable_fact"] == 2
    assert record_counts["student_confirmation"] == 1


def test_loader_skips_generated_archive_outputs_and_learntrace_dir(tmp_path: Path) -> None:
    scenario_dir = SCENARIOS_DIR / "05-add-tests-confirmed"
    generated_records = tmp_path / "historical-snapshot.json"

    write_learning_record(
        scenario_dir,
        output_path=tmp_path / "learning-record.md",
        records_output_path=generated_records,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    for source in scenario_dir.glob("*.json"):
        (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    learntrace_dir = tmp_path / ".learntrace"
    learntrace_dir.mkdir()
    (learntrace_dir / "ignored.json").write_text(
        json.dumps({"events": [_load_json(scenario_dir / "observable-event-commit.json")]}),
        encoding="utf-8",
    )
    (tmp_path / "business-events.json").write_text(
        json.dumps({"events": {"foo": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    loaded = load_project_artifacts(tmp_path, validator=validator)
    bundle = build_archive_bundle(
        loaded.events,
        confirmations=loaded.confirmations,
        warnings=loaded.warnings,
        validator=validator,
    )

    assert [event.id for event in bundle.events] == ["evt-s05-1", "evt-s05-2"]
    assert [candidate.to_dict() for candidate in bundle.candidates] == _expected_candidates(
        scenario_dir
    )


def test_stable_candidate_id_regardless_of_order() -> None:
    """Candidate IDs must be stable when input events are reordered."""
    validator = ContractValidator(schema_dir=SCHEMA_DIR)

    scenario_01 = SCENARIOS_DIR / "01-revise-ai-suggestion-confirmed"
    events_01, confirmations_01 = load_project_records(scenario_01, validator=validator)

    # Build bundle with events in original order
    bundle_original = build_archive_bundle(
        events_01,
        confirmations=confirmations_01,
        validator=validator,
    )

    # Build bundle with events in reversed order
    bundle_reversed = build_archive_bundle(
        tuple(reversed(events_01)),
        confirmations=confirmations_01,
        validator=validator,
    )

    # Both bundles must produce the same candidate IDs
    original_ids = [c.id for c in bundle_original.candidates]
    reversed_ids = [c.id for c in bundle_reversed.candidates]
    assert original_ids == reversed_ids


def test_bundle_marker_required_for_container_json(tmp_path: Path) -> None:
    """Container JSON without learntrace_bundle marker should be rejected."""
    (tmp_path / "business.json").write_text(
        json.dumps({"name": "project", "version": "1.0"}),
        encoding="utf-8",
    )

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    with pytest.raises(ValueError, match="expected LearnTrace record"):
        load_project_artifacts(tmp_path, validator=validator)


def test_bundle_marker_accepted_for_container_json(tmp_path: Path) -> None:
    """Container JSON with learntrace_bundle: true should be recognized."""
    scenario_dir = SCENARIOS_DIR / "05-add-tests-confirmed"
    event_records = [
        _load_json(scenario_dir / "observable-event-commit.json"),
    ]
    batch = {
        "learntrace_bundle": True,
        "events": event_records,
    }
    (tmp_path / "bundle.json").write_text(
        json.dumps(batch, ensure_ascii=False),
        encoding="utf-8",
    )

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    loaded = load_project_artifacts(tmp_path, validator=validator)

    assert len(loaded.events) == 1
    assert loaded.events[0].id == "evt-s05-1"


def test_render_markdown_reports_stub_inference_mode() -> None:
    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    events, confirmations = load_project_records(
        SCENARIOS_DIR / "05-add-tests-confirmed",
        validator=validator,
    )
    bundle = build_archive_bundle(events, confirmations=confirmations, validator=validator)
    markdown = render_markdown(bundle, source_dir=SCENARIOS_DIR / "05-add-tests-confirmed")

    assert "候选生成模式：`stub`" in markdown
    assert "本次候选由本地确定性规则生成" in markdown


def test_symlink_json_is_skipped(tmp_path: Path) -> None:
    """Symlinked JSON files outside the project must be skipped."""
    source = SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty" / "observable-event-commit.json"
    (tmp_path / "valid.json").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    loaded = load_project_artifacts(tmp_path, validator=validator)
    assert any(event.id == "evt-s09-1" for event in loaded.events)


def test_identical_duplicate_events_are_deduped() -> None:
    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    events, confirmations = load_project_records(
        SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty",
        validator=validator,
    )

    bundle = build_archive_bundle(
        (*events, events[0]),
        confirmations=confirmations,
        validator=validator,
    )

    assert [event.id for event in bundle.events] == ["evt-s09-1"]


def test_conflicting_duplicate_event_ids_are_rejected() -> None:
    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    events, confirmations = load_project_records(
        SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty",
        validator=validator,
    )
    conflicting_event = replace(events[0], summary=f"{events[0].summary} changed")

    with pytest.raises(ValueError, match="conflicting observable_event"):
        build_archive_bundle(
            (*events, conflicting_event),
            confirmations=confirmations,
            validator=validator,
        )


def test_confirmation_targeting_missing_candidate_is_rejected() -> None:
    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    events, _ = load_project_records(
        SCENARIOS_DIR / "08-no-trace-degraded",
        validator=validator,
    )
    confirmation = StudentConfirmation(
        id="conf-missing-candidate",
        candidate_id="cand-does-not-exist",
        decision=ConfirmationDecision.CONFIRMED,
        student_statement=MissingInfo(),
    )

    with pytest.raises(ValueError, match="confirmation targets missing candidate"):
        build_archive_bundle(
            events,
            confirmations=(confirmation,),
            validator=validator,
        )


def test_inferencer_output_with_missing_basis_event_is_rejected() -> None:
    class MissingBasisInferencer:
        def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
            return (
                CandidateDraft(
                    node_type=NodeType.FOLLOW_UP,
                    statement="候选引用了不存在的事实，应被完整性校验拒绝。",
                    basis_event_ids=("evt-not-present",),
                    uncertainty="高：测试用 inferencer 故意输出缺失依据。",
                    question_to_student="这条候选是否应该出现？",
                ),
            )

    validator = ContractValidator(schema_dir=SCHEMA_DIR)
    events, confirmations = load_project_records(
        SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty",
        validator=validator,
    )

    with pytest.raises(ValueError, match="missing basis event"):
        build_archive_bundle(
            events,
            confirmations=confirmations,
            validator=validator,
            inferencer=MissingBasisInferencer(),
        )


def test_loader_ignores_unrelated_json_and_dependency_dirs(tmp_path: Path) -> None:
    source = SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty" / "observable-event-commit.json"
    (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "unrelated-array.json").write_text("[]", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "demo"}', encoding="utf-8")
    venv_dir = tmp_path / ".venv"
    venv_dir.mkdir()
    (venv_dir / "broken.json").write_text("{", encoding="utf-8")
    opencode_dir = tmp_path / ".opencode" / "skills" / "learntrace-skill"
    opencode_dir.mkdir(parents=True)
    (opencode_dir / "invalid-fixture.json").write_text("{", encoding="utf-8")

    loaded = load_project_artifacts(
        tmp_path,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    assert [event.id for event in loaded.events] == ["evt-s09-1"]
    assert loaded.warnings == ()


def test_empty_events_business_json_is_not_misread_as_learntrace(tmp_path: Path) -> None:
    """An ordinary business JSON with an empty ``events`` list must not be
    treated as a LearnTrace container (previously it passed, and a malformed
    sibling ``warnings`` shape could abort the whole batch)."""
    source = SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty" / "observable-event-commit.json"
    (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "stock.json").write_text(
        json.dumps({"events": [], "warnings": [{"code": "low-stock", "message": "..."}]}),
        encoding="utf-8",
    )

    loaded = load_project_artifacts(
        tmp_path,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    # Only the real LearnTrace event is loaded; the empty-events business JSON
    # is ignored rather than misread as a container and aborting on its
    # non-LearnTrace warning shape.
    assert [event.id for event in loaded.events] == ["evt-s09-1"]


def test_loader_accepts_confirmation_only_container(tmp_path: Path) -> None:
    confirmation = _load_json(
        SCENARIOS_DIR / "01-revise-ai-suggestion-confirmed" / "student-confirmation.json"
    )
    (tmp_path / "confirmations.json").write_text(
        json.dumps({"events": [], "confirmations": [confirmation]}, ensure_ascii=False),
        encoding="utf-8",
    )

    loaded = load_project_artifacts(tmp_path, validator=ContractValidator(schema_dir=SCHEMA_DIR))

    assert loaded.events == ()
    assert [item.to_dict() for item in loaded.confirmations] == [confirmation]


def test_foreign_json_with_unknown_evidence_level_is_skipped(tmp_path: Path) -> None:
    """A foreign JSON that happens to carry an ``evidence_level`` key with a
    non-LearnTrace value must be skipped by the default scan, not swallowed as a
    LearnTrace record (previously any non-null ``evidence_level`` was enough)."""
    source = SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty" / "observable-event-commit.json"
    (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "lesson-plan.json").write_text(
        json.dumps(
            {
                "evidence_level": "standard",
                "subject": "math",
                "score": 92,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_project_artifacts(
        tmp_path,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    # Only the real LearnTrace event is loaded; the foreign "evidence_level"
    # JSON is ignored rather than swallowed as a record and then rejected.
    assert [event.id for event in loaded.events] == ["evt-s09-1"]


def test_strict_inputs_rejects_unrelated_json(tmp_path: Path) -> None:
    source = SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty" / "observable-event-commit.json"
    (tmp_path / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "unrelated-array.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="expected a JSON object"):
        load_project_artifacts(
            tmp_path,
            validator=ContractValidator(schema_dir=SCHEMA_DIR),
            strict_inputs=True,
        )


def test_loader_downgrades_discovered_schema_errors_to_warning(tmp_path: Path) -> None:
    valid_record = (
        SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty" / "observable-event-commit.json"
    )
    (tmp_path / "valid-event.json").write_text(
        valid_record.read_text(encoding="utf-8"), encoding="utf-8"
    )
    bad_record = (
        REPO_ROOT / "tests" / "fixtures" / "golden" / "invalid" / "event-missing-source-refs.json"
    )
    target = tmp_path / "bad-event.json"
    target.write_text(bad_record.read_text(encoding="utf-8"), encoding="utf-8")

    loaded = load_project_artifacts(
        tmp_path,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    assert [event.id for event in loaded.events] == ["evt-s09-1"]
    assert [warning.code for warning in loaded.warnings] == ["invalid_learntrace_input"]
    assert loaded.warnings[0].source == "bad-event.json"
    assert "invalid observable_event" in loaded.warnings[0].message
    assert "source_refs" in loaded.warnings[0].message


def test_strict_inputs_keeps_discovered_schema_errors_fatal(tmp_path: Path) -> None:
    bad_record = (
        REPO_ROOT / "tests" / "fixtures" / "golden" / "invalid" / "event-missing-source-refs.json"
    )
    (tmp_path / "bad-event.json").write_text(
        bad_record.read_text(encoding="utf-8"), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="invalid observable_event"):
        load_project_artifacts(
            tmp_path,
            validator=ContractValidator(schema_dir=SCHEMA_DIR),
            strict_inputs=True,
        )


def test_loader_reports_conflicting_duplicate_event_paths(tmp_path: Path) -> None:
    source_path = (
        SCENARIOS_DIR / "09-sparse-evidence-high-uncertainty" / "observable-event-commit.json"
    )
    source = cast(
        dict[str, Any],
        _load_json(source_path),
    )
    conflicting = dict(source)
    conflicting["summary"] = f"{source['summary']}（冲突副本）"

    (tmp_path / "event-a.json").write_text(
        json.dumps(source, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "event-b.json").write_text(
        json.dumps(conflicting, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_project_artifacts(
            tmp_path,
            validator=ContractValidator(schema_dir=SCHEMA_DIR),
        )

    message = str(exc_info.value)
    assert "event-a.json" in message
    assert "event-b.json" in message
    assert "conflicting observable_event records" in message
