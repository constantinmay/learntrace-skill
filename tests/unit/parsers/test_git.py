from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol, cast

import pytest

from learntrace.cli import main
from learntrace.models import ContractValidator, SourceType
from learntrace.parsers import (
    export_git_evidence,
    parse_git_history,
    write_git_file,
    write_git_history_index,
    write_git_tree,
)
from learntrace.parsers import git as git_module


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


def _head(root: Path) -> str:
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


def test_reports_file_rename_and_preserves_linear_history_over_budget(tmp_path: Path) -> None:
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

    assert result.warnings == ()
    summaries = [event.summary for event in result.events]
    assert any("add parser module" in summary for summary in summaries)
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
    merge_event = next(event for event in result.events if "merge feature" in event.summary)
    assert "记录 1 个文件变更（1 个新增）" in merge_event.summary
    assert any("新增文件 src/feature.py" in event.summary for event in result.events)
    assert any("新增文件 src/main.py" in event.summary for event in result.events)
    assert any("add parser module" in event.summary for event in result.events)
    assert [ref.ref for ref in merge_event.source_refs[1:]] == ["src/feature.py"]
    placeholder = next(event for event in result.events if "未逐条展开" in event.summary)
    assert "1 条侧支提交" in placeholder.summary
    details = dict(result.warnings[0].details)
    assert details["retained_first_parent"] == 3
    assert details["retained_merges"] == 1
    assert details["omitted_side_branch"] == 1
    assert details["budget_exceeded_for_boundaries"] is True
    assert details["omitted_first_commit_id"]
    assert details["omitted_last_commit_id"]
    assert details["history_index"] == ".learntrace/evidence/git/history.jsonl"
    assert len(placeholder.source_refs) == 2
    assert result.to_dict()["warnings"][0]["details"]["omitted_side_branch"] == 1

    complete_result = parse_git_history(repository)
    assert complete_result.warnings == ()
    assert any("add feature" in event.summary for event in complete_result.events)


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


def test_ignores_remote_repository_urls(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)
    _git(repository, "remote", "add", "origin", "git@github.com:example/learntrace-fixture.git")

    result = parse_git_history(repository)

    serialized = result.to_dict()
    assert commit_id in str(serialized)
    assert "github.com" not in str(serialized)


def test_exports_full_local_diff_and_before_after_source_on_demand(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    parent_id = _make_repository(repository)
    secret = "sk-abcdefghijklmnop"
    (repository / "src" / "parser.py").write_text(
        f'VALUE = 2\nAPI_KEY = "{secret}"\n',
        encoding="utf-8",
    )
    _git(repository, "add", "src/parser.py")
    _git(repository, "commit", "-q", "-m", "update parser behavior")
    commit_id = _head(repository)
    _git(repository, "remote", "add", "origin", "https://github.com/example/project.git")

    exported = export_git_evidence(
        repository,
        commit_id,
        paths=("src/parser.py",),
    )

    assert exported.commit_id == commit_id
    assert exported.index_path == repository / ".learntrace/evidence/git" / commit_id / "index.json"
    index = json.loads(exported.index_path.read_text(encoding="utf-8"))
    assert index["base_revision"] == parent_id
    assert index["exported_paths"] == ["src/parser.py"]
    assert index["evidence_scope"] == "local_authorized_project"
    assert index["redaction_applied"] is False
    diff = (exported.output_dir / "diff.patch").read_text(encoding="utf-8")
    change = index["changes"][0]
    before = json.loads(
        write_git_file(repository, parent_id, "src/parser.py").output_path.read_text(
            encoding="utf-8"
        )
    )["content"]
    after = json.loads(
        write_git_file(repository, commit_id, "src/parser.py").output_path.read_text(
            encoding="utf-8"
        )
    )["content"]
    assert secret in diff
    assert secret in after
    assert before == "VALUE = 1\n"
    assert all(artifact["truncated"] is False for artifact in index["artifacts"])
    diff_artifact = next(artifact for artifact in index["artifacts"] if artifact["kind"] == "diff")
    assert diff_artifact["hunks"] == [
        {
            "repository_path": "src/parser.py",
            "old_start": 1,
            "old_lines": 1,
            "new_start": 1,
            "new_lines": 2,
        }
    ]
    assert change["hunks"] == [
        {
            "old": {
                "start": 1,
                "end": 1,
                "lines": 1,
                "readback": (
                    f"learntrace git-file <project> {parent_id} src/parser.py --lines 1:1"
                ),
            },
            "new": {
                "start": 1,
                "end": 2,
                "lines": 2,
                "readback": (
                    f"learntrace git-file <project> {commit_id} src/parser.py --lines 1:2"
                ),
            },
        }
    ]
    assert change["before"]["object_id"]
    assert change["after"]["object_id"]


def test_git_evidence_index_records_artifact_truncation(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    (repository / "src" / "parser.py").write_text("VALUE = 2\n" * 20, encoding="utf-8")
    _git(repository, "add", "src/parser.py")
    _git(repository, "commit", "-q", "-m", "expand parser")

    exported = export_git_evidence(
        repository,
        _head(repository),
        output_dir=repository / ".learntrace/evidence/truncated",
        max_chars=20,
    )

    index = json.loads(exported.index_path.read_text(encoding="utf-8"))
    assert any(artifact["truncated"] is True for artifact in index["artifacts"])
    assert len((exported.output_dir / "diff.patch").read_text(encoding="utf-8")) == 20


def test_git_evidence_handles_crlf_pure_rename_mode_only_and_binary(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    _git(repository, "config", "core.autocrlf", "false")
    (repository / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    (repository / "rename-me.txt").write_bytes(b"same content\n")
    (repository / "script.sh").write_bytes(b"echo ok\n")
    (repository / "asset.bin").write_bytes(b"\x00\x01\x02\x00\x03")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "add edge files")

    (repository / "crlf.txt").write_bytes(b"one\r\nchanged\r\nthree\r\n")
    _git(repository, "add", "crlf.txt")
    _git(repository, "commit", "-q", "-m", "modify CRLF")
    crlf_commit = _head(repository)
    crlf_export = export_git_evidence(repository, crlf_commit, paths=("crlf.txt",))
    crlf_index = json.loads(crlf_export.index_path.read_text())
    crlf_file = json.loads(
        write_git_file(
            repository, crlf_commit, "crlf.txt", start_line=1, end_line=3
        ).output_path.read_text()
    )
    assert len(crlf_index["changes"][0]["hunks"]) == 1
    assert crlf_file["content"] == "one\r\nchanged\r\nthree\r\n"
    assert crlf_file["reached_eof"] is True

    _git(repository, "mv", "rename-me.txt", "renamed.txt")
    _git(repository, "commit", "-q", "-m", "pure rename")
    rename_commit = _head(repository)
    rename_export = export_git_evidence(repository, rename_commit, paths=("renamed.txt",))
    rename_index = json.loads(rename_export.index_path.read_text())
    rename_change = rename_index["changes"][0]
    assert rename_change["status"] == "R"
    assert rename_change["previous_path"] == "rename-me.txt"
    assert rename_change["before"]["object_id"] == rename_change["after"]["object_id"]
    assert rename_change["hunks"] == []
    assert "similarity index 100%" in (rename_export.output_dir / "diff.patch").read_text()

    _git(repository, "update-index", "--chmod=+x", "script.sh")
    _git(repository, "commit", "-q", "-m", "mode only")
    mode_commit = _head(repository)
    mode_export = export_git_evidence(repository, mode_commit, paths=("script.sh",))
    mode_change = json.loads(mode_export.index_path.read_text())["changes"][0]
    assert mode_change["mode_only"] is True
    assert mode_change["before_mode"] == "100644"
    assert mode_change["after_mode"] == "100755"
    assert mode_change["before"]["object_id"] == mode_change["after"]["object_id"]
    assert mode_change["hunks"] == []

    (repository / "asset.bin").write_bytes(b"\x00\x09\x08\x00\x07\x06")
    _git(repository, "add", "asset.bin")
    _git(repository, "commit", "-q", "-m", "binary change")
    binary_commit = _head(repository)
    binary_export = export_git_evidence(repository, binary_commit, paths=("asset.bin",))
    binary_change = json.loads(binary_export.index_path.read_text())["changes"][0]
    assert binary_change["hunks"] == []
    assert binary_change["before"]["size_bytes"] == 5
    assert binary_change["after"]["size_bytes"] == 6
    assert "Binary files" in (binary_export.output_dir / "diff.patch").read_text()


def test_octopus_merge_is_retained_and_compared_with_first_parent(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    base = _make_repository(repository)
    main_branch = subprocess.run(
        ["git", "-C", str(repository), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branches = ("edge-a", "edge-b", "edge-c")
    for branch in branches:
        _git(repository, "checkout", "-q", "-b", branch, base)
        (repository / f"{branch}.txt").write_text(f"{branch}\n", encoding="utf-8")
        _git(repository, "add", f"{branch}.txt")
        _git(repository, "commit", "-q", "-m", f"add {branch}")
    _git(repository, "checkout", "-q", main_branch)
    _git(repository, "merge", "-q", "--no-ff", *branches, "-m", "octopus merge")
    merge_commit = _head(repository)

    result = write_git_history_index(repository)
    records = [json.loads(line) for line in result.output_path.read_text().splitlines()]
    record = next(item for item in records if item.get("commit_id") == merge_commit)

    assert len(record["parents"]) == 4
    assert record["is_merge"] is True
    assert record["evidence_eligible"] is True
    assert {file["path"] for file in record["files"]} == {
        "edge-a.txt",
        "edge-b.txt",
        "edge-c.txt",
    }


def test_writes_complete_searchable_git_history_index(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    first_commit = _make_repository(repository)
    (repository / "src" / "parser.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repository, "add", "src/parser.py")
    _git(repository, "commit", "-q", "-m", "refine parser")
    second_commit = _head(repository)

    result = write_git_history_index(repository)

    records = [
        json.loads(line) for line in result.output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["record_type"] == "history_metadata"
    assert records[0]["head"] == second_commit
    assert records[0]["total_commits"] == 2
    assert records[0]["repository_shallow"] is False
    assert records[0]["history_complete"] is True
    assert records[0]["records_complete"] is True
    assert records[0]["details_truncated"] is False
    assert records[0]["complete"] is True
    assert records[0]["ordering"] == "topological_newest_first"
    assert records[0]["errors"] == []
    assert [record["commit_id"] for record in records[1:]] == [second_commit, first_commit]
    assert records[1]["files"] == [
        {
            "status": "M",
            "path": "src/parser.py",
            "previous_path": None,
            "additions": 1,
            "deletions": 1,
            "roles": ["source"],
        }
    ]
    assert isinstance(records[1]["tree_id"], str)
    assert records[1]["file_count"] == 1
    assert records[1]["top_level_counts"] == {"src": 1}


def test_generated_only_commit_stays_in_topology_but_not_project_evidence(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _make_repository(repository)
    (repository / "nested").mkdir()
    generated = repository / "nested" / "learning-record.md"
    generated.write_text("generated report\n", encoding="utf-8")
    _git(repository, "add", "nested/learning-record.md")
    _git(repository, "commit", "-q", "-m", "store generated report")
    generated_commit = _head(repository)

    parsed = parse_git_history(repository)
    index_result = write_git_history_index(repository)
    records = [json.loads(line) for line in index_result.output_path.read_text().splitlines()]
    commit = next(record for record in records if record.get("commit_id") == generated_commit)
    tree = json.loads(write_git_tree(repository, generated_commit).output_path.read_text())

    assert not any(event.id == f"evt-git-{generated_commit}" for event in parsed.events)
    assert commit["evidence_eligible"] is False
    assert commit["files"] == []
    assert commit["excluded_generated_artifacts"] == {
        "count": 1,
        "paths": ["nested/learning-record.md"],
    }
    assert records[0]["total_commits"] == 2
    assert "nested/learning-record.md" not in {entry["path"] for entry in tree["entries"]}
    assert tree["excluded_generated_artifacts"]["paths"] == ["nested/learning-record.md"]


def test_shallow_history_preserves_visible_commits_but_reports_the_boundary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    first_commit = _make_repository(source)
    (source / "src" / "parser.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(source, "add", "src/parser.py")
    _git(source, "commit", "-q", "-m", "second visible tip")
    tip = _head(source)
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", source.as_uri(), str(clone)],
        check=True,
        capture_output=True,
    )

    parsed = parse_git_history(clone)
    result = write_git_history_index(clone)
    records = [json.loads(line) for line in result.output_path.read_text().splitlines()]
    metadata = records[0]

    assert first_commit not in result.output_path.read_text()
    assert metadata["head"] == tip
    assert metadata["repository_shallow"] is True
    assert metadata["history_complete"] is False
    assert metadata["records_complete"] is True
    assert metadata["complete"] is False
    assert metadata["shallow_boundary_commits"] == [tip]
    assert any(warning.code == "git_history_shallow" for warning in parsed.warnings)


def test_git_evidence_rejects_path_not_changed_by_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)

    with pytest.raises(ValueError, match="paths are not changed"):
        export_git_evidence(repository, commit_id, paths=("README.md",))


def test_git_evidence_reports_timeout_without_leaking_subprocess_error(
    tmp_path: Path,
    monkeypatch: _MonkeyPatch,
) -> None:
    def timeout_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        del root, arguments
        raise subprocess.TimeoutExpired("git", 15)

    monkeypatch.setattr(git_module, "_git", timeout_git)

    with pytest.raises(ValueError, match="Git evidence export timed out"):
        export_git_evidence(tmp_path, "a1b2c3d4")


def test_git_evidence_cli_writes_readback_index(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _make_repository(repository)

    exit_code = main(
        [
            "git-evidence",
            str(repository),
            commit_id,
            "--path",
            "src/parser.py",
        ]
    )

    assert exit_code == 0
    index_path = repository / ".learntrace/evidence/git" / commit_id / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["commit_id"] == commit_id
    assert index["artifacts"][0]["kind"] == "diff"


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
