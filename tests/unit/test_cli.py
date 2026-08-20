import json
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from learntrace import __version__
from learntrace import cli as cli_module
from learntrace.archive import build_parser
from learntrace.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "golden"
    / "scenarios"
    / "09-sparse-evidence-high-uncertainty"
)


def test_version(capsys: CaptureFixture[str]) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

    assert capsys.readouterr().out.strip() == f"learntrace {__version__}"


def test_parser_accepts_project_dir_and_output() -> None:
    args = build_parser().parse_args(
        [
            "sample-project",
            "--output",
            "learning-record.md",
            "--records-output",
            "archive-records.json",
            "--questions-output",
            "learning-questions.md",
            "--confirmations",
            "student-confirmations.json",
            "--strict-inputs",
        ]
    )
    assert args.project_dir == "sample-project"
    assert str(args.output) == "learning-record.md"
    assert str(args.records_output) == "archive-records.json"
    assert str(args.questions_output) == "learning-questions.md"
    assert args.confirmations == [Path("student-confirmations.json")]
    assert args.strict_inputs is True


def test_main_prints_write_summary(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    output_path = tmp_path / "learning-record.md"
    records_output = tmp_path / "archive-records.json"
    questions_output = tmp_path / "learning-questions.md"

    exit_code = main(
        [
            str(SCENARIO_DIR),
            "--output",
            str(output_path),
            "--records-output",
            str(records_output),
            "--questions-output",
            str(questions_output),
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    assert records_output.exists()
    assert questions_output.exists()
    summary = capsys.readouterr().out
    assert "Wrote learning record:" in summary
    assert "Archive fingerprint: sha256:" in summary
    assert "pending_questions=1" in summary


def test_archive_subcommand_forwards_legacy_options(tmp_path: Path) -> None:
    output_path = tmp_path / "learning-record.md"

    exit_code = main(["archive", str(SCENARIO_DIR), "--output", str(output_path)])

    assert exit_code == 0
    assert output_path.is_file()


def test_archive_subcommand_exposes_archive_help(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["archive", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--confirmations" in help_text
    assert "--strict-inputs" in help_text


def test_run_parser_accepts_confirmation_files() -> None:
    args = cli_module.build_parser().parse_args(
        [
            "run",
            "sample-project",
            "--confirmations",
            "first.json",
            "--confirmations",
            "second.json",
        ]
    )

    assert args.confirmations == [Path("first.json"), Path("second.json")]


def test_main_reports_missing_records_without_traceback(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    (tmp_path / "package.json").write_text('{"name": "raw-project"}', encoding="utf-8")

    try:
        main([str(tmp_path)])
    except SystemExit as exc:
        assert exc.code == 1

    captured = capsys.readouterr()
    assert "expected LearnTrace record JSON or a Task2 parse-result JSON" in captured.err
    assert "Traceback" not in captured.err


def test_parse_command_discovers_documents_and_writes_events(tmp_path: Path) -> None:
    (tmp_path / "task.md").write_text("# Goal\n\nImplement the parser safely.\n", encoding="utf-8")
    output = tmp_path / "events.json"

    exit_code = main(["parse", str(tmp_path), "--no-git", "--output", str(output)])

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["events"][0]["kind"] == "document"
    assert payload["analysis_scope"]["documents"] == ["task.md"]


def test_adapt_command_requires_explicit_authorization(tmp_path: Path) -> None:
    output = tmp_path / "trace.json"

    exit_code = main(["adapt", str(tmp_path / "missing.json"), "--output", str(output)])

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "not_authorized"
    assert payload["events"] == []


def test_adapt_command_parses_authorized_fixture(tmp_path: Path) -> None:
    output = tmp_path / "trace.json"
    export = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"

    exit_code = main(
        ["adapt", str(export), "--project-root", str(REPO_ROOT), "--authorized", "-o", str(output)]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "parsed"
    assert payload["events"]


def test_run_command_builds_end_to_end_local_outputs(tmp_path: Path) -> None:
    (tmp_path / "task.md").write_text("# Goal\n\nImplement the parser safely.\n", encoding="utf-8")

    exit_code = main(["run", str(tmp_path), "--no-git"])

    assert exit_code == 0
    assert (tmp_path / "learning-record.md").is_file()
    assert (tmp_path / ".learntrace" / "task2-result.json").is_file()
    assert (tmp_path / ".learntrace" / "task3-result.json").is_file()
    assert (tmp_path / ".learntrace" / "archive-records.json").is_file()
    assert (tmp_path / ".learntrace" / "learning-questions.md").is_file()

    first = json.loads((tmp_path / ".learntrace" / "task2-result.json").read_text(encoding="utf-8"))
    assert main(["run", str(tmp_path), "--no-git"]) == 0
    second = json.loads(
        (tmp_path / ".learntrace" / "task2-result.json").read_text(encoding="utf-8")
    )
    assert second == first


def test_cli_merges_shorthand_confirmation_file(tmp_path: Path) -> None:
    confirmations_path = tmp_path / "confirmations.json"
    confirmations_path.write_text(
        json.dumps(
            {
                "confirmations": [
                    {
                        "candidate_id": "cand-s09-adjust_constraints-5678a64e29b362f5",
                        "decision": "confirmed",
                        "confirmed_at": "2026-08-20T10:30:00+08:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records_output = tmp_path / "archive.json"

    exit_code = main(
        [
            str(SCENARIO_DIR.parent / "09-sparse-evidence-high-uncertainty"),
            "--confirmations",
            str(confirmations_path),
            "--records-output",
            str(records_output),
            "--output",
            str(tmp_path / "record.md"),
        ]
    )

    assert exit_code == 0
    archive = json.loads(records_output.read_text(encoding="utf-8"))
    confirmation = archive["confirmations"][0]
    assert confirmation["candidate_id"] == "cand-s09-adjust_constraints-5678a64e29b362f5"
    assert confirmation["student_statement"] == {"status": "not_recorded"}
    assert confirmation["confirmed_at"] == "2026-08-20T10:30:00+08:00"


def test_cli_rejects_confirmation_without_real_timestamp(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    confirmations_path = tmp_path / "confirmations.json"
    confirmations_path.write_text(
        json.dumps(
            {
                "confirmations": [
                    {
                        "candidate_id": "cand-s09-adjust_constraints-5678a64e29b362f5",
                        "decision": "confirmed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                str(SCENARIO_DIR),
                "--confirmations",
                str(confirmations_path),
                "--output",
                str(tmp_path / "record.md"),
            ]
        )

    assert exc_info.value.code == 1
    assert "confirmed_at" in capsys.readouterr().err
