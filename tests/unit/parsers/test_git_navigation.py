from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import NoReturn

import pytest

import learntrace.parsers.git_file as git_file_module
import learntrace.parsers.git_tree as git_tree_module
import learntrace.parsers.git_worktree as git_worktree_module
from learntrace.cli import main
from learntrace.parsers import (
    discover_static_materials,
    write_git_file,
    write_git_history_index,
    write_git_tree,
    write_git_worktree,
)


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repository(root: Path) -> str:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "LearnTrace Test")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "module.py").write_text(
        "FIRST = 1\nSECOND = 2\nTHIRD = 3\nFOURTH = 4\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_module.py").write_text(
        "def test_second():\n    assert 2 == 2\n",
        encoding="utf-8",
    )
    (root / "asset.bin").write_bytes(b"\x00\x01\x02")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "add source and test")
    return _git(root, "rev-parse", "HEAD")


def _payload(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_git_tree_writes_complete_version_layout(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)

    result = write_git_tree(repository, commit_id)
    payload = _payload(result.output_path)

    assert payload["revision"] == commit_id
    assert payload["file_count"] == 3
    assert payload["top_level_counts"] == {"asset.bin": 1, "src": 1, "tests": 1}
    entries = {entry["path"]: entry for entry in payload["entries"]}  # type: ignore[index]
    assert entries["src/module.py"]["roles"] == ["source"]
    assert entries["tests/test_module.py"]["roles"] == ["test"]
    assert entries["asset.bin"]["content_kind"] == "binary"
    assert entries["asset.bin"]["available"] is False

    discovered = discover_static_materials(repository)
    assert discovered.inventory.tracked_files == (
        "asset.bin",
        "src/module.py",
        "tests/test_module.py",
    )
    assert discovered.inventory.metadata_only_files == ("asset.bin",)


def test_git_file_reads_bounded_historical_and_worktree_ranges(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)

    historical = write_git_file(
        repository,
        commit_id,
        "src/module.py",
        start_line=2,
        end_line=3,
    )
    history_payload = _payload(historical.output_path)
    assert history_payload["content"] == "SECOND = 2\nTHIRD = 3\n"
    assert history_payload["start_line"] == 2
    assert history_payload["end_line"] == 3
    assert history_payload["total_lines"] == 4
    assert history_payload["truncated"] is True
    locator = history_payload["locator"]
    assert isinstance(locator, dict)
    assert locator["revision"] == commit_id
    assert locator["path"] == "src/module.py"
    assert isinstance(locator["object_id"], str)

    (repository / "src" / "module.py").write_bytes(b"WORKTREE = 1\n")
    current = write_git_file(repository, "worktree", "src/module.py")
    current_payload = _payload(current.output_path)
    assert current_payload["revision"] == "worktree"
    assert current_payload["content"] == "WORKTREE = 1\n"
    assert isinstance(current_payload["object_id"], str)

    past_end = _payload(
        write_git_file(
            repository,
            commit_id,
            "src/module.py",
            start_line=100,
            end_line=110,
        ).output_path
    )
    assert past_end["available"] is True
    assert past_end["content"] == ""
    assert past_end["end_line"] is None
    assert past_end["truncated"] is True

    with pytest.raises(ValueError, match="at most 200 lines"):
        write_git_file(repository, commit_id, "src/module.py", start_line=1, end_line=201)


def test_git_file_marks_binary_non_utf8_and_lfs_pointer_unavailable(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    (repository / "legacy.txt").write_bytes(b"\xff\xfe")
    (repository / "large.dat").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:0123456789abcdef\nsize 123456\n",
        encoding="utf-8",
    )

    binary = _payload(write_git_file(repository, "worktree", "asset.bin").output_path)
    non_utf8 = _payload(write_git_file(repository, "worktree", "legacy.txt").output_path)
    lfs = _payload(write_git_file(repository, "worktree", "large.dat").output_path)

    assert binary["content_kind"] == "binary"
    assert binary["unavailable_reason"] == "binary"
    assert non_utf8["content_kind"] == "non_utf8"
    assert non_utf8["unavailable_reason"] == "non_utf8"
    assert lfs["content_kind"] == "git_lfs_pointer"
    assert lfs["unavailable_reason"] == "git_lfs_object_not_loaded"


def test_git_file_records_missing_file_and_rejects_invalid_ranges(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)

    missing = _payload(write_git_file(repository, commit_id, "src/missing.py").output_path)
    assert missing["available"] is False
    assert missing["unavailable_reason"] == "not_present"

    with pytest.raises(ValueError, match="start line must be at least 1"):
        write_git_file(repository, commit_id, "src/module.py", start_line=0)
    with pytest.raises(ValueError, match="end line must not be before"):
        write_git_file(repository, commit_id, "src/module.py", start_line=3, end_line=2)
    with pytest.raises(ValueError, match="invalid Git revision"):
        write_git_file(repository, "--help", "src/module.py")
    with pytest.raises(ValueError, match="must stay inside"):
        write_git_file(
            repository,
            commit_id,
            "src/module.py",
            output_path=tmp_path / "outside.json",
        )


def test_git_tree_identifies_tracked_lfs_pointer(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    (repository / "large.dat").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:0123456789abcdef\nsize 123456\n"
    )
    _git(repository, "add", "large.dat")
    _git(repository, "commit", "-q", "-m", "track lfs pointer")

    payload = _payload(write_git_tree(repository, "HEAD").output_path)
    entries = {entry["path"]: entry for entry in payload["entries"]}  # type: ignore[index]
    assert entries["large.dat"]["content_kind"] == "git_lfs_pointer"
    assert entries["large.dat"]["available"] is False
    assert entries["large.dat"]["unavailable_reason"] == "git_lfs_object_not_loaded"


def test_git_worktree_indexes_staged_unstaged_and_untracked_without_reading_untracked(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    (repository / "tests" / "test_module.py").write_text("STAGED = True\n", encoding="utf-8")
    _git(repository, "add", "tests/test_module.py")
    (repository / "src" / "module.py").write_text("UNSTAGED = True\n", encoding="utf-8")
    secret = "UNTRACKED-SHOULD-NOT-BE-IN-INDEX"
    (repository / "notes.txt").write_text(secret, encoding="utf-8")
    (repository / ".learntrace").mkdir()
    (repository / ".learntrace" / "self-generated.json").write_text("{}", encoding="utf-8")
    (repository / "learning-record.md").write_text("generated", encoding="utf-8")

    result = write_git_worktree(repository)
    payload = _payload(result.output_path)
    entries = {entry["path"]: entry for entry in payload["entries"]}  # type: ignore[index]

    assert entries["tests/test_module.py"]["staged"] is True
    assert entries["src/module.py"]["unstaged"] is True
    assert entries["notes.txt"]["untracked"] is True
    assert ".learntrace/self-generated.json" not in entries
    assert "learning-record.md" not in entries
    assert secret not in result.output_path.read_text(encoding="utf-8")
    assert payload["counts"] == {
        "total": 3,
        "staged": 1,
        "unstaged": 1,
        "untracked": 1,
        "conflicts": 0,
    }
    assert len(payload["staged_diff"]["sha256"]) == 64  # type: ignore[index]
    assert len(payload["unstaged_diff"]["sha256"]) == 64  # type: ignore[index]


def test_history_index_links_source_and_test_as_non_causal_same_commit(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)

    result = write_git_history_index(repository)
    records = [json.loads(line) for line in result.output_path.read_text().splitlines()]
    record = next(item for item in records if item.get("commit_id") == commit_id)

    assert record["relations"] == [
        {
            "type": "same_commit",
            "source_paths": ["src/module.py"],
            "test_paths": ["tests/test_module.py"],
            "causal": False,
        }
    ]


def test_navigation_cli_commands_write_agent_readable_artifacts(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)

    assert main(["git-tree", str(repository), commit_id]) == 0
    assert (
        main(
            [
                "git-file",
                str(repository),
                commit_id,
                "src/module.py",
                "--lines",
                "2:3",
            ]
        )
        == 0
    )
    assert main(["git-worktree", str(repository)]) == 0


@pytest.mark.parametrize(
    ("module", "function", "message"),
    [
        (git_file_module, write_git_file, "Git file read timed out"),
        (git_tree_module, write_git_tree, "Git tree read timed out"),
        (git_worktree_module, write_git_worktree, "Git worktree read timed out"),
    ],
)
def test_navigation_public_apis_wrap_git_timeouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    function: object,
    message: str,
) -> None:
    def timeout(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise subprocess.TimeoutExpired("git", 15)

    internal_name = {
        git_file_module: "_write_git_file",
        git_tree_module: "_write_git_tree",
        git_worktree_module: "_write_git_worktree",
    }[module]
    monkeypatch.setattr(module, internal_name, timeout)
    with pytest.raises(ValueError, match=message):
        if function is write_git_file:
            write_git_file(tmp_path, "HEAD", "src/module.py")
        elif function is write_git_tree:
            write_git_tree(tmp_path, "HEAD")
        else:
            write_git_worktree(tmp_path)


@pytest.mark.parametrize(
    ("module", "function"),
    [
        (git_file_module, write_git_file),
        (git_tree_module, write_git_tree),
        (git_worktree_module, write_git_worktree),
    ],
)
def test_navigation_public_apis_sanitize_os_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    function: object,
) -> None:
    def denied(*args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        raise PermissionError(13, "denied", "private-source")

    internal_name = {
        git_file_module: "_write_git_file",
        git_tree_module: "_write_git_tree",
        git_worktree_module: "_write_git_worktree",
    }[module]
    monkeypatch.setattr(module, internal_name, denied)
    with pytest.raises(ValueError) as exc_info:
        if function is write_git_file:
            write_git_file(tmp_path, "HEAD", "src/module.py")
        elif function is write_git_tree:
            write_git_tree(tmp_path, "HEAD")
        else:
            write_git_worktree(tmp_path)
    assert "private-source" not in str(exc_info.value)
