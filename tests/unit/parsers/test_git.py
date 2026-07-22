from __future__ import annotations

import os
import subprocess
from pathlib import Path

from learntrace.models import ContractValidator, SourceType
from learntrace.parsers import parse_git_history


def _git(root: Path, *arguments: str, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=env,
    )


def _make_repository(root: Path) -> str:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Fixture User")
    _git(root, "config", "user.email", "fixture@example.invalid")
    (root / "src").mkdir()
    (root / "src" / "parser.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", "src/parser.py")
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-20T10:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-20T10:00:00+08:00"
    _git(root, "commit", "-q", "-m", "add parser module", env=commit_env)
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_parses_git_commit_as_neutral_observable_fact(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)

    result = parse_git_history(repository)

    assert result.warnings == ()
    assert len(result.events) == 1
    event = result.events[0]
    assert event.id == f"evt-git-{commit_id}"
    assert event.occurred_at == "2026-07-20T10:00:00+08:00"
    assert "学生" not in event.summary
    assert event.source_refs[0].type is SourceType.GIT_COMMIT
    assert event.source_refs[0].ref == commit_id
    assert event.source_refs[1].ref == "src/parser.py"
    assert ContractValidator().is_valid("observable_event", event.to_dict())


def test_reports_non_git_directory(tmp_path: Path) -> None:
    result = parse_git_history(tmp_path)

    assert result.events == ()
    assert result.warnings[0].code == "not_git_repository"
