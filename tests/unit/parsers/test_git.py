from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol, cast

import pytest

from learntrace.models import ContractValidator, SourceType
from learntrace.parsers import git as git_module
from learntrace.parsers import list_git_authors, parse_git_history, read_git_commit_text


class _MonkeyPatch(Protocol):
    def setattr(self, target: object, name: str, value: object) -> None: ...

    def chdir(self, path: str | os.PathLike[str]) -> None: ...

    def setenv(self, name: str, value: str) -> None: ...


class _GitRunner(Protocol):
    def __call__(
        self,
        root: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]: ...


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


def test_uses_trusted_absolute_git_when_project_contains_fake_executable(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    resolver = cast(object, vars(git_module)["_resolve_git_executable"])
    assert callable(resolver)
    trusted_git = resolver(repository)
    assert isinstance(trusted_git, Path)

    fake_git = repository / ("git.exe" if os.name == "nt" else "git")
    fake_git.write_text("this project file must never be executed\n", encoding="utf-8")
    if os.name != "nt":
        fake_git.chmod(0o755)
    monkeypatch.chdir(repository)
    monkeypatch.setenv("PATH", f"{repository}{os.pathsep}{trusted_git.parent}")

    result = parse_git_history(repository)

    assert result.warnings == ()
    assert result.events


def test_rejects_project_root_that_is_only_a_parent_repository_subdirectory(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "parent-repository"
    _make_repository(parent)
    project = parent / "target-project"
    project.mkdir()
    sibling = parent / "PRIVATE_OUTSIDE_PROJECT.txt"
    sibling.write_text("must not appear in Task2 output\n", encoding="utf-8")
    _git(parent, "add", "PRIVATE_OUTSIDE_PROJECT.txt")
    _git(parent, "commit", "-q", "-m", "add sibling file")

    result = parse_git_history(project)

    assert result.events == ()
    assert [warning.code for warning in result.warnings] == ["git_root_mismatch"]
    assert "PRIVATE_OUTSIDE_PROJECT.txt" not in str(result.to_dict())


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

    result = parse_git_history(repository, max_commits=2, find_copies_harder=True)

    summaries = [event.summary for event in result.events]
    assert any("复制为 src/copy.py" in summary for summary in summaries)
    assert any("删除文件 src/copy.py" in summary for summary in summaries)


def test_reports_merge_changes_relative_to_first_parent(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    _git(repository, "checkout", "-q", "-b", "feature")
    (repository / "src" / "feature.py").write_text("FEATURE = True\n", encoding="utf-8")
    _git(repository, "add", "src/feature.py")
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-21T11:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-21T11:00:00+08:00"
    _git(repository, "commit", "-q", "-m", "add feature", env=commit_env)

    _git(repository, "checkout", "-q", "-")
    (repository / "src" / "main.py").write_text("MAIN = True\n", encoding="utf-8")
    _git(repository, "add", "src/main.py")
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-22T12:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-22T12:00:00+08:00"
    _git(repository, "commit", "-q", "-m", "add main module", env=commit_env)
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-23T13:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-23T13:00:00+08:00"
    _git(repository, "merge", "-q", "--no-ff", "feature", "-m", "merge feature", env=commit_env)

    result = parse_git_history(repository, max_commits=1)

    assert [warning.code for warning in result.warnings] == ["git_history_truncated"]
    assert len(result.events) == 2
    assert "记录 1 个文件变更（1 个新增）" in result.events[0].summary
    assert "新增文件 src/feature.py" in result.events[1].summary
    assert all("src/main.py" not in event.summary for event in result.events)
    assert [ref.ref for ref in result.events[0].source_refs[1:]] == ["src/feature.py"]


def test_reports_unavailable_line_counts_for_binary_file(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    (repository / "asset.bin").write_bytes(b"\x00\x01\x02\xff")
    _git(repository, "add", "asset.bin")
    commit_env = dict(os.environ)
    commit_env["GIT_AUTHOR_DATE"] = "2026-07-21T11:00:00+08:00"
    commit_env["GIT_COMMITTER_DATE"] = "2026-07-21T11:00:00+08:00"
    _git(repository, "commit", "-q", "-m", "add binary fixture", env=commit_env)

    result = parse_git_history(repository, max_commits=1)

    binary_event = next(event for event in result.events if "asset.bin" in event.summary)
    assert "行数统计不可用" in binary_event.summary


def test_enables_expensive_copy_search_only_when_requested(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    real_git = cast(_GitRunner, vars(git_module)["_git"])
    calls: list[tuple[str, ...]] = []

    def record_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return real_git(root, *arguments)

    monkeypatch.setattr(git_module, "_git", record_git)

    parse_git_history(repository)
    default_diff_calls = [call for call in calls if call and call[0] == "diff-tree"]
    assert default_diff_calls
    assert all("--find-copies-harder" not in call for call in default_diff_calls)

    calls.clear()
    parse_git_history(repository, find_copies_harder=True)
    stronger_diff_calls = [call for call in calls if call and call[0] == "diff-tree"]
    assert stronger_diff_calls
    assert all("--find-copies-harder" in call for call in stronger_diff_calls)


def test_reports_history_os_error_after_repository_check(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    calls = 0

    def fail_after_repository_check(
        root: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        del root, arguments
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess([], 0, "true\n", "")
        raise PermissionError(13, "Permission denied", "private-history")

    monkeypatch.setattr(git_module, "_git", fail_after_repository_check)

    result = parse_git_history(tmp_path)

    assert result.events == ()
    assert result.warnings[0].code == "git_read_error"
    assert "private-history" not in result.warnings[0].message


def test_reports_commit_metadata_os_error_without_stopping_history(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)
    real_git = cast(_GitRunner, vars(git_module)["_git"])

    def fail_metadata(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        if arguments and arguments[0] == "show":
            raise PermissionError(13, "Permission denied", "private-metadata")
        return real_git(root, *arguments)

    monkeypatch.setattr(git_module, "_git", fail_metadata)

    result = parse_git_history(repository)

    assert result.events == ()
    assert result.warnings[0].code == "git_commit_read_error"
    assert result.warnings[0].source == commit_id
    assert "private-metadata" not in result.warnings[0].message


def test_reports_malformed_commit_metadata(tmp_path: Path, monkeypatch: _MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)
    real_git = cast(_GitRunner, vars(git_module)["_git"])

    def malformed_metadata(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        if arguments and arguments[0] == "show":
            return subprocess.CompletedProcess(arguments, 0, "malformed metadata\n", "")
        return real_git(root, *arguments)

    monkeypatch.setattr(git_module, "_git", malformed_metadata)

    result = parse_git_history(repository)

    assert result.events == ()
    assert result.warnings[0].code == "git_commit_format_error"
    assert result.warnings[0].source == commit_id


def test_reports_commit_diff_returncode_error(tmp_path: Path, monkeypatch: _MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)
    real_git = cast(_GitRunner, vars(git_module)["_git"])

    def fail_diff(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        if arguments and arguments[0] == "diff-tree":
            return subprocess.CompletedProcess(arguments, 1, "", "diff unavailable")
        return real_git(root, *arguments)

    monkeypatch.setattr(git_module, "_git", fail_diff)

    result = parse_git_history(repository)

    assert result.events == ()
    assert result.warnings[0].code == "git_commit_read_error"
    assert result.warnings[0].source == commit_id
    assert result.warnings[0].message == "diff unavailable"


def test_reports_commit_diff_timeout(tmp_path: Path, monkeypatch: _MonkeyPatch) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)
    real_git = cast(_GitRunner, vars(git_module)["_git"])

    def timeout_diff(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        if arguments and arguments[0] == "diff-tree":
            raise subprocess.TimeoutExpired("git diff-tree", 15)
        return real_git(root, *arguments)

    monkeypatch.setattr(git_module, "_git", timeout_diff)

    result = parse_git_history(repository)

    assert result.events == ()
    assert result.warnings[0].code == "git_commit_timeout"
    assert result.warnings[0].source == commit_id


def test_rejects_non_positive_commit_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_commits must be at least 1"):
        parse_git_history(tmp_path, max_commits=0)


def _commit_as(root: Path, message: str, *, author: str, email: str, when: str) -> None:
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = author
    env["GIT_AUTHOR_EMAIL"] = email
    env["GIT_AUTHOR_DATE"] = when
    env["GIT_COMMITTER_DATE"] = when
    _git(root, "commit", "-q", "--allow-empty", "-m", message, env=env)


def _make_two_author_repository(root: Path) -> None:
    """两个作者的仓库：学生 2 个提交，协作方 1 个提交（最新）。"""
    repository = root / "repository"
    _make_repository(repository)
    _commit_as(
        repository,
        "add feature",
        author="Student One",
        email="student@example.invalid",
        when="2026-07-21T11:00:00+08:00",
    )
    _commit_as(
        repository,
        "review fix",
        author="Teammate",
        email="teammate@example.invalid",
        when="2026-07-22T12:00:00+08:00",
    )


def test_author_filter_keeps_only_matching_author_commits(tmp_path: Path) -> None:
    _make_two_author_repository(tmp_path)

    result = parse_git_history(tmp_path / "repository", author="Student One")

    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.code == "git_author_filtered"
    assert warning.source == "."
    # 初始提交（Fixture User）与协作方提交都被过滤，只剩学生本人的 1 个
    assert "2 个不属于作者" in warning.message
    assert "Student One" in warning.message
    commit_summaries = [event.summary for event in result.events if event.id.startswith("evt-git-")]
    assert len(commit_summaries) == 1
    assert "add feature" in commit_summaries[0]
    assert all("review fix" not in summary for summary in commit_summaries)


def test_author_filter_matches_email_and_is_case_insensitive(tmp_path: Path) -> None:
    _make_two_author_repository(tmp_path)

    by_email = parse_git_history(tmp_path / "repository", author="student@example.invalid")
    assert any(warning.code == "git_author_filtered" for warning in by_email.warnings)
    summaries = [event.summary for event in by_email.events if event.id.startswith("evt-git-")]
    assert any("add feature" in summary for summary in summaries)
    assert not any("review fix" in summary for summary in summaries)

    by_case = parse_git_history(tmp_path / "repository", author="STUDENT ONE")
    assert [warning.code for warning in by_case.warnings] == ["git_author_filtered"]
    commit_summaries = [
        event.summary for event in by_case.events if event.id.startswith("evt-git-")
    ]
    assert any("add feature" in summary for summary in commit_summaries)
    assert not any("review fix" in summary for summary in commit_summaries)


def test_author_filter_without_matches_reports_boundary_and_empty_events(tmp_path: Path) -> None:
    _make_two_author_repository(tmp_path)

    result = parse_git_history(tmp_path / "repository", author="Nobody Else")

    assert result.events == ()
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.code == "git_author_filtered"
    assert "3 个不属于作者" in warning.message
    assert "Nobody Else" in warning.message


def test_author_filter_uses_full_history_before_window_truncation(tmp_path: Path) -> None:
    """作者过滤作用于全部历史，早期本人提交不会被最新的协作方提交挤出窗口。"""
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Fixture User")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _commit_as(
        repository,
        "first",
        author="Student One",
        email="student@example.invalid",
        when="2026-07-20T10:00:00+08:00",
    )
    _commit_as(
        repository,
        "second",
        author="Student One",
        email="student@example.invalid",
        when="2026-07-21T11:00:00+08:00",
    )
    _commit_as(
        repository,
        "team fix",
        author="Teammate",
        email="teammate@example.invalid",
        when="2026-07-22T12:00:00+08:00",
    )
    _commit_as(
        repository,
        "team fix 2",
        author="Teammate",
        email="teammate@example.invalid",
        when="2026-07-23T13:00:00+08:00",
    )

    result = parse_git_history(repository, author="Student One", max_commits=1)

    commit_summaries = [event.summary for event in result.events if event.id.startswith("evt-git-")]
    # 若先窗口后过滤，max_commits=1 只会看到最新的协作方提交而返回空；
    # 正确语义是先按作者过滤全历史，再截断窗口
    assert len(commit_summaries) == 1
    assert "second" in commit_summaries[0]
    assert [warning.code for warning in result.warnings] == [
        "git_author_filtered",
        "git_history_truncated",
    ]
    assert "2 个不属于作者" in result.warnings[0].message


def test_list_git_authors_aggregates_counts_and_orders_by_commit_count(tmp_path: Path) -> None:
    _make_two_author_repository(tmp_path)

    authors = list_git_authors(tmp_path / "repository")

    assert [(item.name, item.email, item.commits) for item in authors] == [
        ("Fixture User", "fixture@example.invalid", 1),
        ("Student One", "student@example.invalid", 1),
        ("Teammate", "teammate@example.invalid", 1),
    ]
    # 数量相同时按名称排序：让 Student One 多一个提交后应排到最前
    _commit_as(
        tmp_path / "repository",
        "one more",
        author="Student One",
        email="student@example.invalid",
        when="2026-07-23T13:00:00+08:00",
    )
    ranked = list_git_authors(tmp_path / "repository")
    assert (ranked[0].name, ranked[0].commits) == ("Student One", 2)


def test_list_git_authors_is_empty_outside_a_repository(tmp_path: Path) -> None:
    assert list_git_authors(tmp_path) == ()


def test_read_git_commit_text_returns_message_and_diff(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)

    text = read_git_commit_text(repository, commit_id)

    assert "add parser module" in text
    assert commit_id in text
    assert "src/parser.py" in text
    assert "+VALUE = 1" in text


def test_read_git_commit_text_raises_on_unknown_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)

    with pytest.raises(ValueError, match="bad object"):
        read_git_commit_text(repository, "0" * 40)
