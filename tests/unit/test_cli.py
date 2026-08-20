from pathlib import Path

from _pytest.capture import CaptureFixture

from learntrace import __version__
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
            "--strict-inputs",
        ]
    )
    assert args.project_dir == "sample-project"
    assert str(args.output) == "learning-record.md"
    assert str(args.records_output) == "archive-records.json"
    assert str(args.questions_output) == "learning-questions.md"
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
