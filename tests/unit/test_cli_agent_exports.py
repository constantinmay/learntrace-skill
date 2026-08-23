"""CLI coverage for the Claude Code and Codex trace export flags."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from learntrace.cli import main

REPO_ROOT = Path(__file__).parents[2]
CLAUDE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "claude-code" / "authorized-session.jsonl"
CODEX_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "codex" / "authorized-session.jsonl"


@pytest.fixture()
def sample_project(tmp_path: Path) -> Path:
    (tmp_path / "task.md").write_text("# Goal\n\nImplement the feature safely.\n", encoding="utf-8")
    return tmp_path


def test_adapt_dispatches_claude_code_source(sample_project: Path, tmp_path: Path) -> None:
    output = tmp_path / "trace-result.json"

    exit_code = main(
        [
            "adapt",
            str(CLAUDE_FIXTURE),
            "--source",
            "claude-code",
            "--authorized",
            "-o",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "parsed"
    assert len(payload["events"]) == 3
    assert all(event["source_refs"][0]["note"] == "claude-code" for event in payload["events"])


def test_adapt_dispatches_codex_source(sample_project: Path, tmp_path: Path) -> None:
    output = tmp_path / "trace-result.json"

    exit_code = main(
        [
            "adapt",
            str(CODEX_FIXTURE),
            "--source",
            "codex",
            "--authorized",
            "-o",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "parsed"
    assert len(payload["events"]) == 3
    assert all(event["source_refs"][0]["note"] == "codex" for event in payload["events"])


def test_run_accepts_authorized_claude_code_export(
    sample_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "run",
            str(sample_project),
            "--no-git",
            "--claude-code-export",
            str(CLAUDE_FIXTURE),
            "--authorize-claude-code-export",
            str(CLAUDE_FIXTURE),
        ]
    )

    assert exit_code == 0
    trace = json.loads(
        (sample_project / ".learntrace" / "task3-result.json").read_text(encoding="utf-8")
    )
    assert trace["status"] == "parsed"
    assert len(trace["events"]) == 3
    captured = capsys.readouterr()
    assert "status=parsed" in captured.out


def test_run_merges_opencode_and_claude_code_exports(sample_project: Path) -> None:
    opencode_fixture = REPO_ROOT / "tests" / "fixtures" / "opencode" / "authorized-export.json"

    exit_code = main(
        [
            "run",
            str(sample_project),
            "--no-git",
            "--opencode-export",
            str(opencode_fixture),
            "--authorize-opencode-export",
            str(opencode_fixture),
            "--claude-code-export",
            str(CLAUDE_FIXTURE),
            "--authorize-claude-code-export",
            str(CLAUDE_FIXTURE),
        ]
    )

    assert exit_code == 0
    trace = json.loads(
        (sample_project / ".learntrace" / "task3-result.json").read_text(encoding="utf-8")
    )
    assert trace["status"] == "parsed"
    notes = {event["source_refs"][0]["note"] for event in trace["events"]}
    assert notes == {"opencode", "claude-code"}


def test_run_rejects_legacy_authorized_flag_with_new_exports(
    sample_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                str(sample_project),
                "--no-git",
                "--authorized",
                "--claude-code-export",
                str(CLAUDE_FIXTURE),
            ]
        )

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "--authorized only covers the OpenCode export" in captured.err


def test_run_without_new_host_authorization_reports_not_authorized(
    sample_project: Path,
) -> None:
    exit_code = main(
        [
            "run",
            str(sample_project),
            "--no-git",
            "--claude-code-export",
            str(CLAUDE_FIXTURE),
        ]
    )

    assert exit_code == 0
    trace = json.loads(
        (sample_project / ".learntrace" / "task3-result.json").read_text(encoding="utf-8")
    )
    assert trace["status"] == "not_authorized"
    assert trace["events"] == []
    assert any(warning["code"] == "host_input_not_authorized" for warning in trace["warnings"])


def test_run_reports_unauthorized_host_alongside_parsed_host(sample_project: Path) -> None:
    """A parsed host must not hide that another host's input went unread."""

    exit_code = main(
        [
            "run",
            str(sample_project),
            "--no-git",
            "--claude-code-export",
            str(CLAUDE_FIXTURE),
            "--authorize-claude-code-export",
            str(CLAUDE_FIXTURE),
            "--codex-export",
            str(CODEX_FIXTURE),
        ]
    )

    assert exit_code == 0
    trace = json.loads(
        (sample_project / ".learntrace" / "task3-result.json").read_text(encoding="utf-8")
    )
    assert trace["status"] == "parsed"
    assert len(trace["events"]) == 3
    host_warning = next(
        warning for warning in trace["warnings"] if warning["code"] == "host_input_not_authorized"
    )
    assert host_warning["location"] == "codex"
    assert "未获授权" in host_warning["message"]
    # With multiple contributing hosts every warning location is host-prefixed.
    assert all(
        warning["location"].startswith(("claude-code.", "codex", "trace_merge"))
        for warning in trace["warnings"]
    )


def test_run_reports_missing_authorized_host_file_alongside_parsed_host(
    sample_project: Path,
) -> None:
    missing_codex = sample_project / "missing-codex-session.jsonl"

    exit_code = main(
        [
            "run",
            str(sample_project),
            "--no-git",
            "--claude-code-export",
            str(CLAUDE_FIXTURE),
            "--authorize-claude-code-export",
            str(CLAUDE_FIXTURE),
            "--codex-export",
            str(missing_codex),
            "--authorize-codex-export",
            str(missing_codex),
        ]
    )

    assert exit_code == 0
    trace = json.loads(
        (sample_project / ".learntrace" / "task3-result.json").read_text(encoding="utf-8")
    )
    assert trace["status"] == "parsed"
    host_warning = next(
        warning for warning in trace["warnings"] if warning["code"] == "host_authorized_not_found"
    )
    assert host_warning["location"] == "codex"
    assert "未找到" in host_warning["message"]


@pytest.mark.parametrize(
    "conflicting_args",
    [
        ["--claude-code-export", "session.jsonl"],
        ["--authorize-claude-code-export", "session.jsonl"],
        ["--codex-export", "session.jsonl"],
        ["--authorize-codex-export", "session.jsonl"],
    ],
)
def test_run_confirmation_stage_rejects_agent_trace_flags(
    sample_project: Path,
    capsys: pytest.CaptureFixture[str],
    conflicting_args: list[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                str(sample_project),
                "--confirmations",
                "confirmations.json",
                *conflicting_args,
            ]
        )

    assert exc_info.value.code == 1
    error = capsys.readouterr().err
    assert "reuses the existing analysis snapshot" in error
    assert "cannot be combined with evidence collection options" in error
