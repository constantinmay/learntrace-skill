from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from learntrace.archive import load_project_artifacts, load_project_records, write_learning_record
from learntrace.models import (
    ConfirmationDecision,
    ContractValidator,
    MissingInfo,
    NodeType,
    ObservableEvent,
    StudentConfirmation,
)
from learntrace.reporting import (
    CandidateDraft,
    build_archive_bundle,
    bundle_to_dict,
    render_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas" / "v0"
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
    assert "未见授权轨迹，未据此推断 AI 使用。" in markdown_08

    events_09, confirmations_09 = load_project_records(
        scenario_09,
        validator=validator,
    )
    bundle_09 = build_archive_bundle(events_09, confirmations=confirmations_09, validator=validator)
    markdown_09 = render_markdown(bundle_09, source_dir=scenario_09)
    assert "待补充 cand-s09：增加 --ignore-missing 是出于什么考虑？" in markdown_09


def test_cli_writes_learning_record(tmp_path: Path) -> None:
    output_path = tmp_path / "learning-record.md"
    write_learning_record(
        SCENARIOS_DIR / "01-revise-ai-suggestion-confirmed",
        output_path=output_path,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

    assert output_path.exists()
    markdown = output_path.read_text(encoding="utf-8")
    assert "学生可能判断 AI 建议的默认类型推断不适合含千分位的数据" in markdown


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
    assert pending_questions[0]["candidate_id"] == "cand-s09"
    quality_checks = cast(dict[str, bool], records["quality_checks"])
    risk_flags = cast(dict[str, bool], records["risk_flags"])
    assert quality_checks["schema_valid"] is True
    assert risk_flags["has_pending_questions"] is True
    assert "cand-s09" in questions_output.read_text(encoding="utf-8")


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
    assert git_source["candidate_ids"] == ["cand-s05"]
    assert candidate_links == [
        {
            "candidate_id": "cand-s05",
            "node_type": "add_tests",
            "basis_event_ids": ["evt-s05-1", "evt-s05-2"],
            "confirmation_id": "conf-s05",
            "status": "resolved",
        }
    ]


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
        json.dumps({"events": [{"id": "evt-001", "summary": "business event"}]}),
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

    loaded = load_project_artifacts(
        tmp_path,
        validator=ContractValidator(schema_dir=SCHEMA_DIR),
    )

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


def test_loader_reports_schema_errors_with_source_path(tmp_path: Path) -> None:
    bad_record = (
        REPO_ROOT / "tests" / "fixtures" / "golden" / "invalid" / "event-missing-source-refs.json"
    )
    target = tmp_path / "bad-event.json"
    target.write_text(bad_record.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_project_artifacts(
            tmp_path,
            validator=ContractValidator(schema_dir=SCHEMA_DIR),
        )

    message = str(exc_info.value)
    assert "bad-event.json" in message
    assert "invalid observable_event" in message
    assert "source_refs" in message
