import json
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from learntrace import __version__
from learntrace import cli as cli_module
from learntrace.archive import build_parser
from learntrace.cli import main
from learntrace.models import MissingInfo, NodeType, ObservableEvent
from learntrace.reporting import CandidateDraft, LLMInferenceError, StubCandidateInferencer
from learntrace.reporting.llm import LLMConfig, OpenAIChatCandidateInferencer

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


def test_run_falls_back_after_llm_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    inferencer = OpenAIChatCandidateInferencer(LLMConfig(api_key="test-key"))

    def fail_inference(events: tuple[ObservableEvent, ...]) -> tuple[CandidateDraft, ...]:
        raise LLMInferenceError("LLM response could not be parsed")

    monkeypatch.setattr(inferencer, "infer", fail_inference)

    monkeypatch.setattr(
        "learntrace.reporting.llm.default_candidate_inferencer",
        lambda: inferencer,
    )
    (tmp_path / "task.md").write_text("# Goal\n\nBuild safely.\n", encoding="utf-8")

    exit_code = main(["run", str(tmp_path), "--no-git"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    archive = json.loads(
        (tmp_path / ".learntrace" / "archive-records.json").read_text(encoding="utf-8")
    )
    assert [warning["code"] for warning in archive["warnings"]] == [
        "llm_inference_failed",
        "llm_fallback_to_stub",
    ]
    assert archive["candidate_inference_mode"] == "llm_stub_fallback"


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
        "learntrace.reporting.llm.default_candidate_inferencer",
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Requesting an OpenCode export that adapts to zero events must emit a
    stderr warning so a silently-empty trace archive is not mistaken for a
    successful trace import."""
    monkeypatch.setattr(
        "learntrace.reporting.llm.default_candidate_inferencer",
        lambda: StubCandidateInferencer(),
    )
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """When OpenCode yields 0 events but a co-requested host (Claude Code)
    parses successfully, the OpenCode zero-events warning must still appear.
    The warning is scoped to OpenCode's own adapter status, not the merged
    multi-host status (which would hide a silently-empty OpenCode export)."""
    monkeypatch.setattr(
        "learntrace.reporting.llm.default_candidate_inferencer",
        lambda: StubCandidateInferencer(),
    )
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
    monkeypatch: pytest.MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """When OpenCode is not requested at all and only a co-host (Claude Code)
    is requested/authorized, no OpenCode zero-events warning must be emitted."""
    monkeypatch.setattr(
        "learntrace.reporting.llm.default_candidate_inferencer",
        lambda: StubCandidateInferencer(),
    )
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
