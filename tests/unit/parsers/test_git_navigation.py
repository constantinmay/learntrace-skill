from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn, cast

import pytest

import learntrace.parsers.git_file as git_file_module
import learntrace.parsers.git_tree as git_tree_module
import learntrace.parsers.git_worktree as git_worktree_module
from learntrace.artifacts import register_generated_artifacts
from learntrace.cli import main
from learntrace.parsers import (
    discover_static_materials,
    write_git_file,
    write_git_history_index,
    write_git_tree,
    write_git_worktree,
)
from learntrace.parsers import git as git_module


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
    assert history_payload["range"] == {"start_line": 2, "end_line": 3}
    assert history_payload["total_lines"] is None
    assert history_payload["continuation"] == {"next_line": 4}
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
    assert cast(dict[str, object], past_end["range"])["end_line"] is None
    assert past_end["reached_eof"] is True
    assert past_end["truncated"] is True

    with pytest.raises(ValueError, match="at most 200 lines"):
        write_git_file(repository, commit_id, "src/module.py", start_line=1, end_line=201)


def test_git_file_byte_pages_can_reconstruct_a_very_long_line(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    original = ("字节分页-" * 100_000).encode()
    (repository / "src" / "generated.txt").write_bytes(original)

    line_page = _payload(
        write_git_file(
            repository, "worktree", "src/generated.txt", start_line=1, end_line=1
        ).output_path
    )
    assert line_page["interrupted_reason"] == "response_page_limit"
    assert line_page["content_bytes"] == 1_000_000
    assert line_page["continuation"] == {"next_byte": 1_000_000, "next_line": 1}

    pieces: list[bytes] = []
    start = 0
    while True:
        end = min(start + 400_000, len(original) + 1)
        payload = _payload(
            write_git_file(
                repository,
                "worktree",
                "src/generated.txt",
                start_byte=start,
                end_byte=end,
            ).output_path
        )
        if "content" in payload:
            pieces.append(str(payload["content"]).encode())
        else:
            import base64

            pieces.append(base64.b64decode(str(payload["content_base64"])))
        if payload["reached_eof"]:
            assert payload["continuation"] is None
            break
        continuation = cast(dict[str, object], payload["continuation"])
        next_byte = continuation["next_byte"]
        assert isinstance(next_byte, int)
        start = next_byte

    assert b"".join(pieces) == original

    beyond_end = _payload(
        write_git_file(
            repository,
            "worktree",
            "src/generated.txt",
            start_byte=len(original) + 100,
            end_byte=len(original) + 200,
        ).output_path
    )
    assert beyond_end["content"] == ""
    assert beyond_end["reached_eof"] is True


def test_git_file_reads_the_staged_blob_not_the_worktree_copy(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    source = repository / "src" / "module.py"
    source.write_bytes(b"STAGED = True\n")
    _git(repository, "add", "src/module.py")
    source.write_bytes(b"WORKTREE = True\n")

    staged = _payload(write_git_file(repository, "index", "src/module.py").output_path)
    current = _payload(write_git_file(repository, "worktree", "src/module.py").output_path)

    assert staged["revision"] == "index"
    assert staged["content"] == "STAGED = True\n"
    assert current["content"] == "WORKTREE = True\n"
    assert staged["object_id"] != current["object_id"]


def test_git_file_discards_worktree_content_if_it_changes_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    real_hash = cast(Callable[[Path, str], str | None], vars(git_file_module)["_hash_worktree"])
    calls = 0

    def changing_hash(root: Path, path: str) -> str | None:
        nonlocal calls
        calls += 1
        value = real_hash(root, path)
        return value if calls == 1 else "0" * 40

    monkeypatch.setattr(git_file_module, "_hash_worktree", changing_hash)
    payload = _payload(write_git_file(repository, "worktree", "src/module.py").output_path)

    assert payload["available"] is False
    assert payload["unavailable_reason"] == "content_changed_during_read"
    assert "content" not in payload


def test_git_file_reports_when_worktree_object_id_cannot_be_computed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)

    def unavailable_object_id(root: Path, path: str) -> None:
        del root, path

    monkeypatch.setattr(git_file_module, "_hash_worktree", unavailable_object_id)

    payload = _payload(write_git_file(repository, "worktree", "src/module.py").output_path)

    assert payload["available"] is False
    assert payload["unavailable_reason"] == "worktree_object_id_unavailable"


def test_git_file_discards_index_content_if_it_changes_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    real_object_id = cast(
        Callable[[Path, str], str | None], vars(git_file_module)["_index_object_id"]
    )
    calls = 0

    def changing_object_id(root: Path, path: str) -> str | None:
        nonlocal calls
        calls += 1
        value = real_object_id(root, path)
        return value if calls == 1 else "0" * 40

    monkeypatch.setattr(git_file_module, "_index_object_id", changing_object_id)
    payload = _payload(write_git_file(repository, "index", "src/module.py").output_path)

    assert payload["available"] is False
    assert payload["unavailable_reason"] == "content_changed_during_read"
    assert "content" not in payload


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
    assert non_utf8["unavailable_reason"] == "non_utf8_text_decode"
    assert lfs["content_kind"] == "git_lfs_pointer"
    assert lfs["unavailable_reason"] == "git_lfs_object_not_loaded"


def test_git_file_records_missing_file_and_rejects_invalid_ranges(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)

    missing = _payload(write_git_file(repository, commit_id, "src/missing.py").output_path)
    assert missing["available"] is False
    assert missing["unavailable_reason"] == "not_present"
    missing_index = _payload(write_git_file(repository, "index", "missing.txt").output_path)
    assert missing_index["unavailable_reason"] == "not_present_in_index"
    directory = _payload(write_git_file(repository, "worktree", "src").output_path)
    assert directory["unavailable_reason"] == "not_a_file"
    (repository / "empty.txt").write_bytes(b"")
    empty = _payload(write_git_file(repository, "worktree", "empty.txt").output_path)
    assert empty["reached_eof"] is True
    assert empty["total_lines"] == 0

    with pytest.raises(ValueError, match="start line must be at least 1"):
        write_git_file(repository, commit_id, "src/module.py", start_line=0)
    with pytest.raises(ValueError, match="end line must not be before"):
        write_git_file(repository, commit_id, "src/module.py", start_line=3, end_line=2)
    with pytest.raises(ValueError, match="invalid Git revision"):
        write_git_file(repository, "--help", "src/module.py")
    with pytest.raises(ValueError, match="byte range must"):
        write_git_file(repository, commit_id, "src/module.py", start_byte=0)
    with pytest.raises(ValueError, match="at most 1000000 bytes"):
        write_git_file(
            repository,
            commit_id,
            "src/module.py",
            start_byte=0,
            end_byte=1_000_001,
        )
    with pytest.raises(ValueError, match="must stay inside"):
        write_git_file(
            repository,
            commit_id,
            "src/module.py",
            output_path=tmp_path / "outside.json",
        )


def test_git_tree_does_not_scan_blob_contents_to_identify_lfs(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    (repository / "large.dat").write_bytes(
        b"version https://git-lfs.github.com/spec/v1\noid sha256:0123456789abcdef\nsize 123456\n"
    )
    _git(repository, "add", "large.dat")
    _git(repository, "commit", "-q", "-m", "track lfs pointer")

    payload = _payload(write_git_tree(repository, "HEAD").output_path)
    entries = {entry["path"]: entry for entry in payload["entries"]}  # type: ignore[index]
    assert entries["large.dat"]["content_kind"] == "unknown"
    assert entries["large.dat"]["available"] is True
    file_payload = _payload(write_git_file(repository, "HEAD", "large.dat").output_path)
    assert file_payload["content_kind"] == "git_lfs_pointer"
    assert "pointer" not in file_payload


def test_git_tree_keeps_submodule_location_as_an_explicit_gap(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)
    _git(
        repository,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{commit_id},vendor/library",
    )
    _git(repository, "commit", "-q", "-m", "record submodule pointer")

    payload = _payload(write_git_tree(repository, "HEAD").output_path)
    raw_entries = payload["entries"]
    assert isinstance(raw_entries, list)
    entries = cast(list[dict[str, Any]], raw_entries)
    entry = next(item for item in entries if item["path"] == "vendor/library")

    assert entry["mode"] == "160000"
    assert entry["object_type"] == "commit"
    assert entry["object_id"] == commit_id
    assert entry["available"] is False
    assert entry["unavailable_reason"] == "submodule_content_not_in_parent_repository"
    historical = _payload(write_git_file(repository, "HEAD", "vendor/library").output_path)
    staged = _payload(write_git_file(repository, "index", "vendor/library").output_path)
    assert historical["unavailable_reason"] == "not_a_blob"
    assert staged["unavailable_reason"] == "not_a_blob"


def test_git_tree_preserves_non_utf8_path_bytes_as_an_explicit_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    tree_id = "a" * 40
    object_id = "b" * 40

    def fake_run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        del root, arguments
        return subprocess.CompletedProcess([], 0, f"{tree_id}\n", "")

    def fake_run_git_bytes(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        del root, arguments
        raw = f"100644 blob {object_id} 12\t".encode() + b"src/legacy-\xff.py\0"
        return subprocess.CompletedProcess([], 0, raw, b"")

    monkeypatch.setattr(git_tree_module, "run_git", fake_run_git)
    monkeypatch.setattr(git_tree_module, "run_git_bytes", fake_run_git_bytes)
    read_entries = cast(
        Callable[[Path, str], tuple[str, list[dict[str, Any]], list[str]]],
        vars(git_tree_module)["_read_tree_entries"],
    )

    actual_tree_id, entries, excluded = read_entries(repository, "HEAD")

    assert actual_tree_id == tree_id
    assert excluded == []
    assert entries == [
        {
            "path": r"src/legacy-\xff.py",
            "path_encoding": "non_utf8",
            "path_bytes_base64": "c3JjL2xlZ2FjeS3/LnB5",
            "mode": "100644",
            "object_type": "blob",
            "object_id": object_id,
            "size": 12,
            "content_kind": "unknown",
            "roles": [],
            "available": False,
            "unavailable_reason": "non_utf8_repository_path",
        }
    ]


def test_git_file_byte_page_preserves_continuation_when_read_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    monkeypatch.setattr(git_file_module, "git_timeout_seconds", lambda: -1)

    payload = _payload(
        write_git_file(
            repository,
            "worktree",
            "src/module.py",
            start_byte=0,
            end_byte=10,
        ).output_path
    )

    assert payload["interrupted_reason"] == "read_timeout"
    assert payload["continuation"] == {"next_byte": 0}
    assert payload["reached_eof"] is False


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
    assert payload["staged_diff"]["complete"] is True  # type: ignore[index]
    assert "git-file <project> index" in payload["staged_readback"]["command"]  # type: ignore[index]


def test_git_worktree_excludes_registered_custom_output_from_entries_and_diff(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    custom = repository / "reports" / "course-review.txt"
    custom.parent.mkdir()
    custom.write_text("initial generated report\n", encoding="utf-8")
    _git(repository, "add", "reports/course-review.txt")
    _git(repository, "commit", "-q", "-m", "track legacy generated output")
    register_generated_artifacts(repository, (custom,))
    custom.write_text("SECRET-GENERATED-CHANGE\n", encoding="utf-8")
    (repository / "src" / "module.py").write_bytes(b"REAL = True\n")

    result = write_git_worktree(repository)
    payload = _payload(result.output_path)
    serialized = result.output_path.read_text(encoding="utf-8")
    entries = cast(list[dict[str, Any]], payload["entries"])

    assert "reports/course-review.txt" not in {entry["path"] for entry in entries}
    assert payload["excluded_generated_artifacts"] == {
        "count": 2,
        "paths": [
            ".learntrace/generated-artifacts.json",
            "reports/course-review.txt",
        ],
    }
    assert "SECRET-GENERATED-CHANGE" not in serialized
    assert "REAL = True" in serialized


def test_git_worktree_disables_repository_fsmonitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    real_run_git = cast(
        Callable[..., subprocess.CompletedProcess[str]], vars(git_worktree_module)["run_git"]
    )
    calls: list[tuple[str, ...]] = []

    def recording_run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return real_run_git(root, *arguments)

    monkeypatch.setattr(git_worktree_module, "run_git", recording_run_git)
    write_git_worktree(repository)

    status_call = next(call for call in calls if "status" in call)
    assert status_call[:3] == ("-c", "core.fsmonitor=false", "status")


def test_streamed_git_preview_reports_command_failure_as_incomplete(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)

    payload = git_module.stream_git_text_preview(
        repository,
        "show",
        "--format=",
        "revision-that-does-not-exist",
        max_chars=64,
    )

    assert payload["available"] is False
    assert payload["complete"] is False
    assert payload["truncated"] is True
    assert payload["unavailable_reason"]
    with pytest.raises(ValueError, match="max_chars must be at least 1"):
        git_module.stream_git_text_preview(repository, "status", max_chars=0)


def test_generated_and_git_internal_paths_are_explicitly_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    commit_id = _repository(repository)
    (repository / "nested").mkdir()
    (repository / "nested" / "learning-questions.md").write_text("generated")

    with pytest.raises(ValueError, match="git_internal_path"):
        write_git_file(repository, "worktree", ".git/config")
    with pytest.raises(ValueError, match="learntrace_generated_artifact"):
        write_git_file(repository, "worktree", "nested/learning-questions.md")

    tree = _payload(write_git_tree(repository, commit_id).output_path)
    excluded = cast(dict[str, object], tree["excluded_generated_artifacts"])
    assert excluded["count"] == 0


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
