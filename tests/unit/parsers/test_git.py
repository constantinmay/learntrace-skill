from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
    assert len(result.events) == 2
    event = result.events[0]
    assert event.id == f"evt-git-{commit_id}"
    assert event.occurred_at == "2026-07-20T10:00:00+08:00"
    assert "学生" not in event.summary
    assert event.source_refs[0].type is SourceType.GIT_COMMIT
    assert event.source_refs[0].ref == commit_id
    assert event.source_refs[1].ref == "src/parser.py"
    file_event = result.events[1]
    assert "新增文件 src/parser.py" in file_event.summary
    assert "新增 1 行、删除 0 行" in file_event.summary
    assert all(
        ContractValidator().is_valid("observable_event", item.to_dict()) for item in result.events
    )


def test_reports_non_git_directory(tmp_path: Path) -> None:
    result = parse_git_history(tmp_path)

    assert result.events == ()
    assert result.warnings[0].code == "not_git_repository"


def test_reports_file_rename_and_history_limit(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    _git(repository, "mv", "src/parser.py", "src/static_parser.py")
    (repository / "src" / "new.py").write_text("NEW = True\n", encoding="utf-8")
    _git(repository, "add", "src/new.py")
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-21T11:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-21T11:00:00+08:00"
    _git(repository, "commit", "-q", "-m", "rename parser and add module", env=commit_env)

    result = parse_git_history(repository, max_commits=1)

    assert [warning.code for warning in result.warnings] == ["git_history_truncated"]
    summaries = [event.summary for event in result.events]
    assert any("重命名为 src/static_parser.py" in summary for summary in summaries)
    assert any("新增文件 src/new.py" in summary for summary in summaries)
    renamed = next(event for event in result.events if "重命名为" in event.summary)
    assert [ref.ref for ref in renamed.source_refs[1:]] == [
        "src/static_parser.py",
        "src/parser.py",
    ]


def test_reports_git_repository_without_commits(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")

    result = parse_git_history(repository)

    assert result.events == ()
    assert result.warnings[0].code == "git_no_commits"


def test_reports_copy_and_delete_file_changes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    shutil.copyfile(repository / "src" / "parser.py", repository / "src" / "copy.py")
    _git(repository, "add", "src/copy.py")
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-21T11:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-21T11:00:00+08:00"
    _git(repository, "commit", "-q", "-m", "copy parser", env=commit_env)
    (repository / "src" / "copy.py").unlink()
    _git(repository, "add", "src/copy.py")
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-22T12:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-22T12:00:00+08:00"
    _git(repository, "commit", "-q", "-m", "remove copy", env=commit_env)

    result = parse_git_history(repository, max_commits=2)

    summaries = [event.summary for event in result.events]
    assert any("复制为 src/copy.py" in summary for summary in summaries)
    assert any("删除文件 src/copy.py" in summary for summary in summaries)


def test_rejects_non_positive_commit_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_commits must be at least 1"):
        parse_git_history(tmp_path, max_commits=0)
