"""Focused tests for privacy-safe OpenCode trace summaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from learntrace.privacy import (
    normalize_project_path,
    redact_sensitive_text,
    summarize_command,
)


@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("password=hunter2", "hunter2"),
        ("token: abcdefghijklmnop", "abcdefghijklmnop"),
        ("Authorization: Bearer bearer-secret", "bearer-secret"),
        ("https://alice:secret@example.test/path", "alice:secret"),
        ("ghp_abcdefghijklmnopqrstuvwxyz123456", "ghp_"),
        (
            "-----BEGIN PRIVATE KEY-----\nPRIVATE_BODY\n-----END PRIVATE KEY-----",
            "PRIVATE_BODY",
        ),
    ],
)
def test_sensitive_text_is_redacted(raw: str, forbidden: str) -> None:
    redacted = redact_sensitive_text(raw)

    assert forbidden not in redacted
    assert "[REDACTED]" in redacted


def test_redaction_is_idempotent_and_limits_after_redaction() -> None:
    raw = f"password={'x' * 300}"

    once = redact_sensitive_text(raw, limit=24)
    twice = redact_sensitive_text(once, limit=24)

    assert once == twice
    assert len(once) <= 24


@pytest.mark.parametrize(
    ("raw", "root", "expected"),
    [
        ("/workspace/project/src/main.py", Path("/workspace/project"), "src/main.py"),
        (
            "C:\\workspace\\project\\src\\main.py",
            Path("C:/workspace/project"),
            "src/main.py",
        ),
        (
            "\\\\server\\share\\project\\src\\main.py",
            Path("\\\\server\\share\\project"),
            "src/main.py",
        ),
        (
            "//server/share/project/src/main.py",
            Path("\\\\server\\share\\project"),
            "src/main.py",
        ),
        ("src\\main.py", None, "src/main.py"),
        ("/home/alice/private.txt", Path("/workspace/project"), "[outside-project]"),
        ("C:\\Users\\Alice\\private.txt", Path("C:/workspace/project"), "[outside-project]"),
        ("\\Users\\Alice\\private.txt", Path("C:/workspace/project"), "[outside-project]"),
        (
            "\\\\server\\share-other\\private.txt",
            Path("\\\\server\\share"),
            "[outside-project]",
        ),
        ("/home/alice/private.txt", None, "[absolute-path]"),
        ("C:Users\\Alice\\private.txt", None, "[unsafe-path]"),
        ("~/.ssh/id_rsa", None, "[unsafe-path]"),
        ("$HOME/.ssh/id_rsa", None, "[unsafe-path]"),
        ("${HOME}/.ssh/id_rsa", None, "[unsafe-path]"),
        ("%USERPROFILE%/private.txt", None, "[unsafe-path]"),
        ("$env:USERPROFILE\\private.txt", None, "[unsafe-path]"),
        ("file:///home/alice/private.txt", None, "[unsafe-path]"),
        (" /home/alice/private.txt", None, "[unsafe-path]"),
        (" C:/Users/Alice/private.txt", None, "[unsafe-path]"),
        ("%2Fhome%2Falice/private.txt", None, "[unsafe-path]"),
        ("./file:///home/alice/private.txt", None, "[unsafe-path]"),
        ("./C:/Users/Alice/private.txt", None, "[unsafe-path]"),
        ("./ /home/alice/private.txt", None, "[unsafe-path]"),
        ("src/\ud800-private.txt", None, "[unsafe-path]"),
        ("../private.txt", Path("/workspace/project"), "[unsafe-path]"),
        ("src/../../private.txt", Path("/workspace/project"), "[unsafe-path]"),
        ("src/\nprivate.txt", Path("/workspace/project"), "[unsafe-path]"),
    ],
)
def test_paths_are_normalized_without_leaking_host_details(
    raw: str, root: Path | None, expected: str
) -> None:
    assert normalize_project_path(raw, root) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("git status --short", "git status"),
        ("uv run pytest tests/unit -q", "uv run pytest"),
        ("python scripts/check.py --token secret", "python"),
        ("TOKEN=secret git status && echo private", "git status"),
        ("", "unknown-command"),
    ],
)
def test_command_summaries_keep_only_conservative_verbs(raw: str, expected: str) -> None:
    assert summarize_command(raw) == expected
