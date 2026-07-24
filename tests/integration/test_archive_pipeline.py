from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from learntrace.archive import load_project_artifacts, load_project_records, write_learning_record
from learntrace.models import ContractValidator
from learntrace.reporting import build_archive_bundle, bundle_to_dict, render_markdown

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
