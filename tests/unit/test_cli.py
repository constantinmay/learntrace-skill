import json
import subprocess
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from learntrace import __version__
from learntrace import cli as cli_module
from learntrace.archive import build_parser
from learntrace.cli import main
from learntrace.models import MissingInfo, NodeType, ObservableEvent
from learntrace.parsers import ParseResult
from learntrace.reporting import CandidateDraft

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
            "--snapshot",
            "first-archive.json",
            "--strict-inputs",
        ]
    )
    assert args.project_dir == "sample-project"
    assert str(args.output) == "learning-record.md"
    assert str(args.records_output) == "archive-records.json"
    assert str(args.questions_output) == "learning-questions.md"
    assert args.confirmations == [Path("student-confirmations.json")]
    assert args.snapshot == Path("first-archive.json")
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
    assert "--snapshot" in help_text
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


def test_git_evidence_parser_accepts_commit_paths_and_limit() -> None:
    args = cli_module.build_parser().parse_args(
        [
            "git-evidence",
            "sample-project",
            "a1b2c3d4",
            "--path",
            "src/parser.py",
            "--path",
            "tests/test_parser.py",
            "--max-chars",
            "2000000",
        ]
    )

    assert args.project_dir == Path("sample-project")
    assert args.commit == "a1b2c3d4"
    assert args.path == ["src/parser.py", "tests/test_parser.py"]
    assert args.max_chars == 2_000_000


def test_git_index_parser_accepts_local_output() -> None:
    args = cli_module.build_parser().parse_args(
        ["git-index", "sample-project", "--output", ".learntrace/history.jsonl"]
    )

    assert args.project_dir == Path("sample-project")
    assert args.output == Path(".learntrace/history.jsonl")


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


def test_discover_command_lists_scope_without_document_content(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    secret_body = "CONTENT_MUST_NOT_BE_PRINTED"
    (tmp_path / "task.md").write_text(secret_body, encoding="utf-8")
    (tmp_path / "pytest-final.log").write_text("1 passed", encoding="utf-8")
    (tmp_path / "app.py").write_text("print('not executed')", encoding="utf-8")

    assert main(["discover", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["documents"] == ["task.md"]
    assert payload["test_logs"] == ["pytest-final.log"]
    assert payload["inventory_counts"]["source_files"] == 1
    assert "git_authors" not in payload
    assert secret_body not in output


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


def test_adapt_multiple_exports_requires_per_path_authorization(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    output = tmp_path / "trace.json"
    export = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "adapt",
                str(export),
                str(export),
                "--authorized",
                "-o",
                str(output),
            ]
        )

    assert exc_info.value.code == 1
    assert "--authorize-export" in capsys.readouterr().err


def test_adapt_multiple_exports_accepts_repeated_exact_authorization(tmp_path: Path) -> None:
    output = tmp_path / "trace.json"
    export = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"

    exit_code = main(
        [
            "adapt",
            str(export),
            str(export),
            "--authorize-export",
            str(export),
            "-o",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "parsed"
    assert payload["events"]
    assert any(warning["code"] == "duplicate_export_event" for warning in payload["warnings"])


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


@pytest.mark.parametrize(
    "conflicting_args",
    [
        ["--document", "task.md"],
        ["--test-log", "pytest.log"],
        ["--no-git"],
        ["--max-commits", "50"],
        ["--find-copies-harder"],
        ["--author", "Fixture User"],
        ["--opencode-export", "session.json"],
        ["--authorized"],
        ["--authorize-opencode-export", "session.json"],
    ],
)
def test_run_confirmation_stage_rejects_evidence_options(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    conflicting_args: list[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                str(tmp_path),
                "--confirmations",
                "confirmations.json",
                *conflicting_args,
            ]
        )

    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    assert "reuses the existing analysis snapshot" in error
    assert "cannot be combined with evidence collection options" in error


def test_run_with_confirmations_reuses_first_analysis_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirmation must not discard traces or invoke inference a second time."""

    class CountingInferencer:
        inference_mode = "test"

        def __init__(self) -> None:
            self.calls = 0

        def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
            self.calls += 1
            trace = next(event for event in events if event.kind.value == "trace_record")
            return (
                CandidateDraft(
                    node_type=NodeType.FOLLOW_UP,
                    statement="学生可能围绕一次授权轨迹进行了追问。",
                    basis_event_ids=(trace.id,),
                    uncertainty="中：是否形成新的理解仍需学生确认。",
                    question_to_student=MissingInfo(note="请由学生确认。"),
                ),
            )

    inferencer = CountingInferencer()
    monkeypatch.setattr(
        "learntrace.reporting.pipeline.StubCandidateInferencer",
        lambda: inferencer,
    )
    (tmp_path / "task.md").write_text(
        "# Goal\n\nImplement the parser safely.\n",
        encoding="utf-8",
    )
    export = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"

    assert (
        main(
            [
                "run",
                str(tmp_path),
                "--no-git",
                "--opencode-export",
                str(export),
                "--authorized",
            ]
        )
        == 0
    )
    archive_path = tmp_path / ".learntrace" / "archive-records.json"
    first_archive = json.loads(archive_path.read_text(encoding="utf-8"))
    candidate_id = first_archive["candidates"][0]["id"]
    first_trace = json.loads(
        (tmp_path / ".learntrace" / "task3-result.json").read_text(encoding="utf-8")
    )
    confirmations_path = tmp_path / "student-confirmations.json"
    confirmations_path.write_text(
        json.dumps(
            {
                "confirmations": [
                    {
                        "candidate_id": candidate_id,
                        "decision": "confirmed",
                        "confirmed_at": "2026-08-20T10:30:00+08:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "run",
                str(tmp_path),
                "--confirmations",
                str(confirmations_path),
            ]
        )
        == 0
    )

    second_archive = json.loads(archive_path.read_text(encoding="utf-8"))
    second_trace = json.loads(
        (tmp_path / ".learntrace" / "task3-result.json").read_text(encoding="utf-8")
    )
    assert inferencer.calls == 1
    assert second_trace == first_trace
    assert second_archive["candidates"][0]["id"] == candidate_id
    assert second_archive["candidates"][0]["status"] == "resolved"
    assert second_archive["confirmations"][0]["candidate_id"] == candidate_id


def test_run_confirmations_relative_path_resolves_against_project_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The documented flow writes student-confirmations.json into the project
    # directory; a bare filename must resolve there even when the caller runs
    # the CLI from a different working directory.
    class SingleCandidateInferencer:
        inference_mode = "test"

        def infer(self, events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
            document = next(event for event in events if event.kind.value == "document")
            return (
                CandidateDraft(
                    node_type=NodeType.ADD_TESTS,
                    statement="学生可能复盘了项目目标文档。",
                    basis_event_ids=(document.id,),
                    uncertainty="中：需学生确认。",
                    question_to_student=MissingInfo(note="请由学生确认。"),
                ),
            )

    monkeypatch.setattr(
        "learntrace.reporting.pipeline.StubCandidateInferencer",
        SingleCandidateInferencer,
    )
    (tmp_path / "task.md").write_text("# Goal\n\nInspect safely.\n", encoding="utf-8")
    assert main(["run", str(tmp_path), "--no-git"]) == 0
    archive_path = tmp_path / ".learntrace" / "archive-records.json"
    candidate_id = json.loads(archive_path.read_text(encoding="utf-8"))["candidates"][0]["id"]
    (tmp_path / "student-confirmations.json").write_text(
        json.dumps(
            {
                "confirmations": [
                    {
                        "candidate_id": candidate_id,
                        "decision": "confirmed",
                        "confirmed_at": "2026-08-20T10:30:00+08:00",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert main(["run", str(tmp_path), "--confirmations", "student-confirmations.json"]) == 0

    archive = json.loads(archive_path.read_text(encoding="utf-8"))
    assert archive["confirmations"][0]["candidate_id"] == candidate_id


def test_run_multiple_exports_keeps_unauthorized_session_unread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "task.md").write_text("# Goal\n\nInspect safely.\n", encoding="utf-8")
    allowed = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"
    denied = tmp_path / "private-session-must-not-be-read.json"
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == denied:
            raise AssertionError("an unauthorized export must not be read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)

    assert (
        main(
            [
                "run",
                str(tmp_path),
                "--no-git",
                "--opencode-export",
                str(allowed),
                "--opencode-export",
                str(denied),
                "--authorize-opencode-export",
                str(allowed),
            ]
        )
        == 0
    )

    trace = json.loads((tmp_path / ".learntrace" / "task3-result.json").read_text(encoding="utf-8"))
    assert trace["status"] == "parsed"
    assert trace["events"]
    assert "export_not_authorized" in [warning["code"] for warning in trace["warnings"]]
    assert str(denied) not in json.dumps(trace, ensure_ascii=False)


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


def test_run_warns_when_export_produces_zero_events(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """Requesting an OpenCode export that adapts to zero events must emit a
    stderr warning so a silently-empty trace archive is not mistaken for a
    successful trace import."""
    (tmp_path / "task.md").write_text(
        "# Goal\n\nImplement the parser safely.\n",
        encoding="utf-8",
    )
    # A well-formed but event-free export: valid info + zero messages.
    empty_export = tmp_path / "empty-session.json"
    empty_export.write_text(
        json.dumps({"info": {"id": "sess-empty", "version": "1.1"}, "messages": []}),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "run",
            str(tmp_path),
            "--no-git",
            "--opencode-export",
            str(empty_export),
            "--authorize-opencode-export",
            str(empty_export),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "produced 0 events" in captured.err
    assert "status=authorized_not_found" in captured.err


def test_run_warns_when_opencode_zero_events_even_if_other_host_parsed(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """When OpenCode yields 0 events but a co-requested host (Claude Code)
    parses successfully, the OpenCode zero-events warning must still appear.
    The warning is scoped to OpenCode's own adapter status, not the merged
    multi-host status (which would hide a silently-empty OpenCode export)."""
    (tmp_path / "task.md").write_text(
        "# Goal\n\nImplement the parser safely.\n",
        encoding="utf-8",
    )
    # OpenCode export that is well-formed but event-free.
    empty_export = tmp_path / "empty-session.json"
    empty_export.write_text(
        json.dumps({"info": {"id": "sess-empty", "version": "1.1"}, "messages": []}),
        encoding="utf-8",
    )
    # A real Claude Code fixture that adapts to PARSED (3 events).
    cc_fixture = REPO_ROOT / "tests" / "fixtures" / "claude-code" / "authorized-session.jsonl"

    exit_code = main(
        [
            "run",
            str(tmp_path),
            "--no-git",
            "--opencode-export",
            str(empty_export),
            "--authorize-opencode-export",
            str(empty_export),
            "--claude-code-export",
            str(cc_fixture),
            "--authorize-claude-code-export",
            str(cc_fixture),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    # OpenCode's own zero-events warning still emitted despite the other host PARSED.
    assert "produced 0 events" in captured.err
    assert "status=authorized_not_found" in captured.err


def test_run_does_not_warn_when_only_non_opencode_host_requested(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """When OpenCode is not requested at all and only a co-host (Claude Code)
    is requested/authorized, no OpenCode zero-events warning must be emitted."""
    (tmp_path / "task.md").write_text(
        "# Goal\n\nImplement the parser safely.\n",
        encoding="utf-8",
    )
    cc_fixture = REPO_ROOT / "tests" / "fixtures" / "claude-code" / "authorized-session.jsonl"

    exit_code = main(
        [
            "run",
            str(tmp_path),
            "--no-git",
            "--claude-code-export",
            str(cc_fixture),
            "--authorize-claude-code-export",
            str(cc_fixture),
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    # No OpenCode export requested → no OpenCode warning.
    assert "produced 0 events" not in captured.err


def test_discover_command_reports_git_authors_of_a_real_repository(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    (tmp_path / "task.md").write_text("# Goal\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Fixture User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "task.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "add task document"],
        check=True,
        capture_output=True,
    )

    assert main(["discover", str(tmp_path)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["git_available"] is True
    assert payload["git_authors"] == [
        {"name": "Fixture User", "email": "fixture@example.invalid", "commits": 1}
    ]


def test_parse_command_forwards_author_to_parse_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """--author must reach the parser (parse layer filters, not git --author)."""
    (tmp_path / "task.md").write_text("# Goal\n\nImplement safely.\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_parse_static_materials(project_root: Path, **kwargs: object) -> ParseResult:
        captured.update(kwargs)
        return ParseResult()

    monkeypatch.setattr(cli_module, "parse_static_materials", fake_parse_static_materials)
    output = tmp_path / "events.json"

    exit_code = main(
        ["parse", str(tmp_path), "--no-git", "--author", "Student One", "-o", str(output)]
    )

    assert exit_code == 0
    assert captured["git_author"] == "Student One"
    assert output.is_file()


def test_parse_command_leaves_author_unset_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    (tmp_path / "task.md").write_text("# Goal\n\nImplement safely.\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_parse_static_materials(project_root: Path, **kwargs: object) -> ParseResult:
        captured.update(kwargs)
        return ParseResult()

    monkeypatch.setattr(cli_module, "parse_static_materials", fake_parse_static_materials)

    exit_code = main(["parse", str(tmp_path), "--no-git", "-o", str(tmp_path / "events.json")])

    assert exit_code == 0
    assert captured["git_author"] is None


def test_export_evidence_list_prints_empty_index_without_exporting(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    exit_code = main(["export-evidence", str(tmp_path), "--list"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == []
    assert not (tmp_path / ".learntrace" / "evidence").exists()


def test_export_evidence_rejects_invocation_without_flags(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main(["export-evidence", str(tmp_path)])

    assert exc_info.value.code == 1
    assert "needs one of --git-commit, --file, --session-export, or --list" in (
        capsys.readouterr().err
    )


def test_export_evidence_cli_exposes_git_commit_export(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Fixture User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "fixture@example.invalid"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "parser.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "src/parser.py"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "add parser module"],
        check=True,
        capture_output=True,
    )
    commit_id = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()

    exit_code = main(["export-evidence", str(tmp_path), "--git-commit", commit_id])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence_dir"] == (tmp_path / ".learntrace" / "evidence").as_posix()
    (entry,) = payload["exported"]
    assert entry["path"] == f"git/{commit_id[:12]}.diff.txt"
    assert entry["kind"] == "git_commit"
    text = (tmp_path / ".learntrace" / "evidence" / entry["path"]).read_text(encoding="utf-8")
    assert "add parser module" in text


def test_export_evidence_cli_exposes_project_file_export(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    (tmp_path / "pytest-final.log").write_text("api_key=SECRET123\n1 passed\n", encoding="utf-8")

    exit_code = main(["export-evidence", str(tmp_path), "--file", "pytest-final.log"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    (entry,) = payload["exported"]
    assert entry["path"] == "files/pytest-final.log"
    assert entry["kind"] == "project_file"
    text = (tmp_path / ".learntrace" / "evidence" / entry["path"]).read_text(encoding="utf-8")
    assert "api_key=[REDACTED]" in text
    assert "1 passed" in text
    assert "SECRET123" not in text


def test_export_evidence_cli_exposes_session_export(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    export = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"

    exit_code = main(
        [
            "export-evidence",
            str(tmp_path),
            "--session-export",
            str(export),
            "--source",
            "opencode",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    (entry,) = payload["exported"]
    assert entry["path"] == "sessions/opencode-ses_fixture.minimal.jsonl"
    assert entry["authorization"] == "minimal"
    lines = (
        (tmp_path / ".learntrace" / "evidence" / entry["path"]).read_text(encoding="utf-8").strip()
    )
    assert len(lines.splitlines()) == 3
    assert "FORBIDDEN_CHAT_TEXT" not in lines


def test_export_evidence_list_has_no_absolute_session_path(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """评审阻塞: --list 输出(index.json)不得泄露本机绝对会话路径。"""
    export = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"

    main(
        [
            "export-evidence",
            str(tmp_path),
            "--session-export",
            str(export),
            "--source",
            "opencode",
        ]
    )
    capsys.readouterr()  # discard export output
    exit_code = main(["export-evidence", str(tmp_path), "--list"])

    assert exit_code == 0
    listed = json.loads(capsys.readouterr().out)
    (entry,) = listed
    assert entry["source"] == "session export opencode/ses_fixture"
    listed_text = json.dumps(listed, ensure_ascii=False)
    index_text = (tmp_path / ".learntrace" / "evidence" / "index.json").read_text(encoding="utf-8")
    for marker in (str(export.resolve()).replace("\\", "/"), export.resolve().as_posix()):
        assert marker not in listed_text
        assert marker not in index_text


def test_export_evidence_full_requires_authorize_full_read(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    """评审阻塞: full 导出须先有逐文件全文授权记录, 仅改参数不能升级权限。"""
    session = tmp_path / "session.jsonl"
    session.write_text("api_key=SECRET123 secret tail\n", encoding="utf-8")

    # 无授权: --authorization full 被拒
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "export-evidence",
                str(tmp_path),
                "--session-export",
                str(session),
                "--source",
                "codex",
                "--authorization",
                "full",
            ]
        )
    assert exc_info.value.code == 1
    assert "no per-file full-read" in capsys.readouterr().err
    # 原文未被读取/落盘
    assert not (tmp_path / ".learntrace" / "evidence" / "sessions").exists()

    # 记录授权后放行
    exit_code = main(
        [
            "authorize-full-read",
            str(tmp_path),
            "--session-export",
            str(session),
            "--source",
            "codex",
            "--authorized-at",
            "2026-08-20T10:00:00+08:00",
        ]
    )
    assert exit_code == 0
    capsys.readouterr()
    exit_code = main(
        [
            "export-evidence",
            str(tmp_path),
            "--session-export",
            str(session),
            "--source",
            "codex",
            "--authorization",
            "full",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    (entry,) = payload["exported"]
    assert entry["authorization"] == "full"
    assert entry["source"] == "session export codex/session"


def test_authorize_full_read_rejects_missing_file(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "authorize-full-read",
                str(tmp_path),
                "--session-export",
                str(tmp_path / "missing.jsonl"),
                "--source",
                "codex",
                "--authorized-at",
                "2026-08-20T10:00:00+08:00",
            ]
        )
    assert exc_info.value.code == 1
    assert "session export not found" in capsys.readouterr().err


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


def _write_minimal_narrative_fixture(project: Path) -> tuple[Path, Path]:
    """在项目目录写入最小合法 narrative payload 与配套 archive,返回两者路径。"""
    work_dir = project / ".learntrace"
    work_dir.mkdir(parents=True)
    archive_path = work_dir / "archive-records.json"
    archive_path.write_text(
        json.dumps(
            {
                "learntrace_bundle": True,
                "record_counts": {
                    "observable_fact": 2,
                    "candidate_inference": 0,
                    "student_confirmation": 0,
                    "missing_info": 0,
                    "warnings": 0,
                    "pending_questions": 0,
                },
                "events": [
                    {
                        "id": "evt-git-abc1234",
                        "kind": "git",
                        "summary": "初始化仓库。",
                        "source_refs": [{"type": "git", "ref": "abc1234"}],
                    },
                    {
                        "id": "evt-trace-abc1234abcd",
                        "kind": "trace_record",
                        "summary": "OpenCode 工具 write 已完成。",
                        "source_refs": [{"type": "session-export", "ref": "session.json"}],
                    },
                ],
                "candidates": [],
                "confirmations": [],
                "pending_questions": [],
            }
        ),
        encoding="utf-8",
    )
    payload_path = project / "narrative-payload.json"
    payload_path.write_text(
        json.dumps(
            {
                "layer": "narrative_payload",
                "variant": "working",
                "meta": {
                    "project": "demo",
                    "evidence_window": "窗口",
                    "status": "未确认版",
                    "pending_questions": 0,
                    "evidence_gaps": 0,
                },
                "overview": {"text": "概述。", "citations": ["evt-git-abc1234"]},
                "stages": [
                    {
                        "stage_id": "stage-1",
                        "title": "初始化",
                        "goal": "搭建仓库。",
                        "key_changes": [
                            {
                                "kind": "Added",
                                "text": "初始化仓库",
                                "citations": ["evt-git-abc1234"],
                            }
                        ],
                        "citations": ["evt-git-abc1234"],
                    }
                ],
                "turning_points": [],
                "ai_collaboration": {
                    "coverage": "一份授权导出。",
                    "shape": "最小保留。",
                    "focus": "项目根目录。",
                    "episodes": [
                        {
                            "label": "写入",
                            "body": "最小保留记录显示执行了写入。",
                            "citations": ["evt-trace-abc1234abcd"],
                            "derived": False,
                        }
                    ],
                    "boundary": "不建立对应关系。",
                },
                "verification": ["通过 verify-narrative 检查引用。"],
                "reflection": {"status": "student_authored_only", "text": None},
                "takeaways": [],
                "evidence_gaps": [],
            }
        ),
        encoding="utf-8",
    )
    return payload_path, archive_path


def test_render_narrative_registers_output_as_generated_artifact(tmp_path: Path) -> None:
    """render-narrative 的输出必须注册,否则下次 run 会把它当项目文档解析。"""
    project = tmp_path / "proj"
    payload_path, archive_path = _write_minimal_narrative_fixture(project)
    output = project / "narrative-rendered.md"

    assert (
        main(
            [
                "render-narrative",
                str(payload_path),
                str(archive_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )

    registry = json.loads(
        (project / ".learntrace" / "generated-artifacts.json").read_text(encoding="utf-8")
    )
    assert "narrative-rendered.md" in registry["paths"]


def test_render_narrative_registers_cwd_relative_output_consistently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对输出路径按调用者 cwd 写文件;登记必须指向同一实际位置。

    从项目父目录运行并传入 ``proj/report.md``:生成物落在项目根下的
    ``report.md``,注册值也必须是 ``report.md``,否则后续 run 不会排除
    真实生成物,还会错误排除无关的嵌套路径。
    """
    project = tmp_path / "proj"
    payload_path, archive_path = _write_minimal_narrative_fixture(project)
    monkeypatch.chdir(tmp_path)

    assert (
        main(
            [
                "render-narrative",
                str(payload_path),
                str(archive_path),
                "--output",
                "proj/report.md",
            ]
        )
        == 0
    )

    assert (project / "report.md").exists()
    registry = json.loads(
        (project / ".learntrace" / "generated-artifacts.json").read_text(encoding="utf-8")
    )
    assert registry["paths"] == ["report.md"]
