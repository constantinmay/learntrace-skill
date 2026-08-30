"""Git 元数据的确定性只读解析。"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from learntrace.artifacts import EvidencePathPolicy, register_generated_artifacts
from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.parsers._common import compact_text, repo_root, safe_os_error, stable_event_id
from learntrace.parsers.types import ParseResult, ParseWarning

_FIELD_SEPARATOR = "\x1f"
_GIT_TIMEOUT_SECONDS = 15
DEFAULT_EVIDENCE_MAX_CHARS = 1_000_000
_COMMIT_ID_RE = re.compile(r"^[0-9A-Fa-f]{4,64}$")
_DIFF_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@",
    re.MULTILINE,
)
_LS_TREE_ENTRY_RE = re.compile(rb"^([0-9]{6}) ([a-z]+) ([0-9a-f]+)\t")


@dataclass(frozen=True, slots=True)
class GitAuthor:
    """最近提交中出现的一个作者（名称、邮箱、提交数）。"""

    name: str
    email: str
    commits: int


@dataclass(frozen=True, slots=True)
class _GitFileChange:
    status: str
    path: str
    previous_path: str | None
    additions: int | None
    deletions: int | None


@dataclass(frozen=True, slots=True)
class _GitHistoryEntry:
    commit_id: str
    occurred_at: str
    parent_ids: tuple[str, ...]
    subject: str


@dataclass(frozen=True, slots=True)
class GitEvidenceExportResult:
    """一次按需 Git 原文导出的可定位结果。"""

    commit_id: str
    output_dir: Path
    index_path: Path
    artifact_count: int


@dataclass(frozen=True, slots=True)
class GitHistoryIndexResult:
    """完整本地 Git 导航索引的写出结果。"""

    output_path: Path
    total_commits: int
    complete: bool


@dataclass(frozen=True, slots=True)
class _RepositoryHistoryState:
    shallow: bool
    shallow_boundaries: tuple[str, ...]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _git_candidate_names() -> tuple[str, ...]:
    if os.name != "nt":
        return ("git",)
    extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
    normalized = tuple(extension.upper() for extension in extensions if extension.startswith("."))
    return tuple(f"git{extension}" for extension in dict.fromkeys(normalized))


def _resolve_git_executable(root: Path) -> Path:
    """Resolve Git from absolute PATH entries, excluding the analyzed project."""
    for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = raw_entry.strip().strip('"')
        if not entry:
            continue
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        for name in _git_candidate_names():
            candidate = directory / name
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if not resolved.is_file() or _is_within(resolved, root):
                continue
            if os.name != "nt" and not os.access(resolved, os.X_OK):
                continue
            return resolved
    raise FileNotFoundError(errno.ENOENT, "trusted Git executable was not found")


def _git_command(root: Path, *arguments: str) -> list[str]:
    return [
        str(_resolve_git_executable(root)),
        "-C",
        str(root),
        "-c",
        "core.quotepath=false",
        "--no-pager",
        *arguments,
    ]


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _git_command(root, *arguments),
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _git_bytes(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        _git_command(root, *arguments),
        check=False,
        capture_output=True,
        shell=False,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _warning(code: str, root: Path, message: str) -> ParseResult:
    return ParseResult(warnings=(ParseWarning(code, ".", message),))


def _parse_name_status(value: str) -> list[tuple[str, str, str | None]]:
    tokens = [token for token in value.split("\0") if token]
    changes: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(tokens):
        raw_status = tokens[index]
        status = raw_status[:1]
        if status in {"R", "C"}:
            if index + 2 >= len(tokens):
                break
            changes.append((status, tokens[index + 2], tokens[index + 1]))
            index += 3
        else:
            if index + 1 >= len(tokens):
                break
            changes.append((status, tokens[index + 1], None))
            index += 2
    return changes


def _line_count(value: str) -> int | None:
    return int(value) if value.isdigit() else None


def _parse_numstat(value: str) -> dict[str, tuple[int | None, int | None]]:
    tokens = [token for token in value.split("\0") if token]
    stats: dict[str, tuple[int | None, int | None]] = {}
    index = 0
    while index < len(tokens):
        fields = tokens[index].split("\t", maxsplit=2)
        if len(fields) != 3:
            index += 1
            continue
        additions, deletions, path = fields
        if path:
            stats[Path(path).as_posix()] = (_line_count(additions), _line_count(deletions))
            index += 1
            continue
        if index + 2 >= len(tokens):
            break
        new_path = Path(tokens[index + 2]).as_posix()
        stats[new_path] = (_line_count(additions), _line_count(deletions))
        index += 3
    return stats


def _file_changes(
    root: Path,
    commit_id: str,
    *,
    merge_first_parent: str | None = None,
    find_copies_harder: bool = False,
) -> tuple[list[_GitFileChange], str | None]:
    """Read a commit diff, comparing merges only with their first parent."""
    commit_range = (
        (merge_first_parent, commit_id) if merge_first_parent is not None else (commit_id,)
    )
    copy_arguments = ("-C", "--find-copies-harder") if find_copies_harder else ("-C",)
    status_result = _git(
        root,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--name-status",
        "-M",
        *copy_arguments,
        "-r",
        "-z",
        *commit_range,
    )
    stats_result = _git(
        root,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--numstat",
        "-M",
        *copy_arguments,
        "-r",
        "-z",
        *commit_range,
    )
    if status_result.returncode != 0 or stats_result.returncode != 0:
        message = compact_text(status_result.stderr or stats_result.stderr) or "could not read diff"
        return [], message
    stats = _parse_numstat(stats_result.stdout)
    changes = [
        _GitFileChange(
            status=status,
            path=Path(path).as_posix(),
            previous_path=Path(previous).as_posix() if previous is not None else None,
            additions=stats.get(Path(path).as_posix(), (None, None))[0],
            deletions=stats.get(Path(path).as_posix(), (None, None))[1],
        )
        for status, path, previous in _parse_name_status(status_result.stdout)
    ]
    return changes, None


def _change_counts(changes: list[_GitFileChange]) -> str:
    labels = {"A": "新增", "M": "修改", "D": "删除", "R": "重命名", "C": "复制"}
    parts: list[str] = []
    for status in ("A", "M", "D", "R", "C"):
        count = sum(change.status == status for change in changes)
        if count:
            parts.append(f"{count} 个{labels[status]}")
    return "、".join(parts) if parts else "0 个"


def _author_matches(author: str, author_name: str, author_email: str) -> bool:
    """按作者名称或邮箱做整体、大小写不敏感匹配（不用 Git regex，不做子串匹配）。

    用整体匹配而非子串，避免选 ``Alice`` 时误中 ``Malice`` / ``xAlice`` 等
    他人提交混入个人档案。
    """
    needle = author.strip().casefold()
    return bool(needle) and (needle == author_name.casefold() or needle == author_email.casefold())


def _history_commit_authors(
    root: Path,
    *,
    warnings: list[ParseWarning],
) -> list[tuple[str, str, str]] | None:
    """一次性读取全部历史提交的 (hash, author_name, author_email)；失败返回 None。"""
    result = _git(root, "log", "--format=%H%x1f%an%x1f%ae")
    if result.returncode != 0:
        message = compact_text(result.stderr) or "could not read commit authors"
        warnings.append(ParseWarning("git_read_error", ".", message))
        return None
    entries: list[tuple[str, str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.split(_FIELD_SEPARATOR, maxsplit=2)
        if len(fields) != 3 or not fields[0].strip():
            continue
        entries.append((fields[0].strip(), fields[1], fields[2]))
    return entries


def _change_summary(commit_id: str, change: _GitFileChange) -> str:
    short_hash = commit_id[:7]
    if change.status == "A":
        action = f"新增文件 {change.path}"
    elif change.status == "D":
        action = f"删除文件 {change.path}"
    elif change.status == "R" and change.previous_path is not None:
        action = f"将文件 {change.previous_path} 重命名为 {change.path}"
    elif change.status == "C" and change.previous_path is not None:
        action = f"将文件 {change.previous_path} 复制为 {change.path}"
    else:
        action = f"修改文件 {change.path}"
    if change.additions is None or change.deletions is None:
        stats = "行数统计不可用"
    else:
        stats = f"新增 {change.additions} 行、删除 {change.deletions} 行"
    return f"提交 {short_hash} {action}（{stats}）。"


def _read_history_index(root: Path) -> tuple[list[_GitHistoryEntry], str | None]:
    history_format = f"--format=%H{_FIELD_SEPARATOR}%cI{_FIELD_SEPARATOR}%P{_FIELD_SEPARATOR}%s"
    result = _git(root, "log", "--topo-order", history_format, "HEAD")
    if result.returncode != 0:
        return [], compact_text(result.stderr) or "could not read Git history"
    entries: list[_GitHistoryEntry] = []
    for line in result.stdout.splitlines():
        fields = line.split(_FIELD_SEPARATOR, maxsplit=3)
        if len(fields) != 4:
            continue
        commit_id, occurred_at, parent_text, subject = fields
        entries.append(
            _GitHistoryEntry(
                commit_id=commit_id,
                occurred_at=occurred_at,
                parent_ids=tuple(parent_text.split()),
                subject=compact_text(subject) or "（提交信息未记录）",
            )
        )
    return entries, None


def _first_parent_ids(root: Path) -> tuple[set[str], str | None]:
    result = _git(root, "rev-list", "--first-parent", "HEAD")
    if result.returncode != 0:
        return set(), compact_text(result.stderr) or "could not read first-parent history"
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}, None


def _repository_history_state(root: Path) -> _RepositoryHistoryState:
    result = _git(root, "rev-parse", "--is-shallow-repository")
    if result.returncode != 0 or result.stdout.strip() not in {"true", "false"}:
        raise ValueError(compact_text(result.stderr) or "could not determine shallow state")
    shallow = result.stdout.strip() == "true"
    boundaries: tuple[str, ...] = ()
    if shallow:
        shallow_path = _git(root, "rev-parse", "--git-path", "shallow")
        if shallow_path.returncode == 0 and shallow_path.stdout.strip():
            candidate = Path(shallow_path.stdout.strip())
            candidate = candidate if candidate.is_absolute() else root / candidate
            try:
                values = candidate.read_text(encoding="ascii").splitlines()
            except (OSError, UnicodeError):
                values = []
            boundaries = tuple(
                value for value in values if re.fullmatch(r"[0-9a-fA-F]{40,64}", value)
            )
    return _RepositoryHistoryState(shallow=shallow, shallow_boundaries=boundaries)


def _filter_evidence_changes(
    policy: EvidencePathPolicy,
    changes: list[_GitFileChange],
) -> tuple[list[_GitFileChange], list[str]]:
    eligible: list[_GitFileChange] = []
    excluded: set[str] = set()
    for change in changes:
        paths = tuple(path for path in (change.previous_path, change.path) if path is not None)
        reasons = {path: policy.reason(path) for path in paths}
        if any(reason is not None for reason in reasons.values()):
            excluded.update(
                path
                for path, reason in reasons.items()
                if reason == "learntrace_generated_artifact"
            )
            continue
        eligible.append(change)
    return eligible, sorted(excluded)


def _select_history(
    entries: list[_GitHistoryEntry],
    first_parent_ids: set[str],
    max_commits: int | None,
) -> tuple[
    list[_GitHistoryEntry],
    list[_GitHistoryEntry],
    tuple[tuple[str, int | str | bool], ...],
]:
    """Apply a soft detail budget without discarding mainline or merge boundaries."""
    if max_commits is None:
        return entries, [], ()
    merge_ids = {entry.commit_id for entry in entries if len(entry.parent_ids) > 1}
    required_ids = first_parent_ids | merge_ids
    required_count = sum(entry.commit_id in required_ids for entry in entries)
    remaining = max(max_commits - required_count, 0)
    selected_ids = set(required_ids)
    for entry in entries:
        if entry.commit_id in selected_ids:
            continue
        if remaining <= 0:
            break
        selected_ids.add(entry.commit_id)
        remaining -= 1

    selected = [entry for entry in entries if entry.commit_id in selected_ids]
    omitted = [entry for entry in entries if entry.commit_id not in selected_ids]
    details: tuple[tuple[str, int | str | bool], ...] = (
        ("total_commits", len(entries)),
        ("requested_budget", max_commits),
        ("retained_commits", len(selected)),
        ("retained_first_parent", sum(entry.commit_id in first_parent_ids for entry in selected)),
        ("retained_merges", sum(len(entry.parent_ids) > 1 for entry in selected)),
        (
            "retained_side_branch",
            sum(entry.commit_id not in first_parent_ids for entry in selected),
        ),
        ("omitted_commits", len(omitted)),
        ("omitted_side_branch", sum(entry.commit_id not in first_parent_ids for entry in omitted)),
        ("budget_exceeded_for_boundaries", len(selected) > max_commits),
        ("omitted_first_commit_id", omitted[0].commit_id if omitted else ""),
        ("omitted_last_commit_id", omitted[-1].commit_id if omitted else ""),
        ("selection_strategy", "first_parent_and_merge_boundaries"),
    )
    return selected, omitted, details


def _commit_source_ref(commit_id: str) -> SourceRef:
    return SourceRef(type=SourceType.GIT_COMMIT, ref=commit_id)


def _file_source_ref(path: str, *, note: str | None = None) -> SourceRef:
    return SourceRef(type=SourceType.FILE, ref=path, note=note)


def _history_placeholder(
    head_id: str,
    omitted: list[_GitHistoryEntry],
) -> ObservableEvent:
    examples = list(dict.fromkeys(entry.subject for entry in omitted))[:3]
    example_text = "；".join(f"“{subject}”" for subject in examples)
    summary = f"Git 历史另有 {len(omitted)} 条侧支提交因细节预算未逐条展开。"
    if example_text:
        summary += f"代表性提交信息：{example_text}。"
    source_refs = (
        SourceRef(
            type=SourceType.GIT_COMMIT,
            ref=omitted[0].commit_id,
            note="省略范围在拓扑顺序中的起点",
        ),
        SourceRef(
            type=SourceType.GIT_COMMIT,
            ref=omitted[-1].commit_id,
            note="省略范围在拓扑顺序中的终点",
        ),
    )
    return ObservableEvent(
        id=stable_event_id(
            "git-history-omitted",
            head_id,
            str(len(omitted)),
            omitted[0].commit_id,
            omitted[-1].commit_id,
        ),
        kind=EventKind.GIT_COMMIT,
        summary=summary,
        source_refs=source_refs,
    )


def _resolve_evidence_commit(root: Path, revision: str) -> str:
    if not _COMMIT_ID_RE.fullmatch(revision):
        raise ValueError("commit must be a hexadecimal Git object id")
    resolved = _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if resolved.returncode != 0 or not resolved.stdout.strip():
        raise ValueError("commit does not identify a commit in this repository")
    commit_id = resolved.stdout.strip()
    reachable = _git(root, "merge-base", "--is-ancestor", commit_id, "HEAD")
    if reachable.returncode != 0:
        raise ValueError("commit is not reachable from HEAD")
    return commit_id


def _safe_repository_path(value: str) -> str:
    path = value.replace("\\", "/")
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or re.match(r"^[A-Za-z]:", path)
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 for character in path)
    ):
        raise ValueError(f"unsafe repository path: {value!r}")
    return path


def _artifact_target(output_dir: Path, relative_path: str) -> Path:
    target = output_dir / Path(relative_path)
    resolved_output = output_dir.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if not _is_within(resolved_target, resolved_output):
        raise ValueError("evidence artifact path escapes the output directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _write_text_artifact(
    output_dir: Path,
    relative_path: str,
    content: str,
    *,
    max_chars: int,
) -> dict[str, object]:
    from learntrace.parsers.git_navigation import atomic_write_text

    truncated = len(content) > max_chars
    written = content[:max_chars]
    target = _artifact_target(output_dir, relative_path)
    atomic_write_text(target, written)
    return {
        "artifact_path": Path(relative_path).as_posix(),
        "available": True,
        "truncated": truncated,
        "source_chars": len(content),
        "written_chars": len(written),
    }


def _stream_commit_diff(
    root: Path,
    commit_id: str,
    parent_id: str | None,
    paths: tuple[str, ...],
    *,
    repository_path: str,
    preview_chars: int,
) -> tuple[str, list[dict[str, int | str]], int, bool, str | None]:
    path_arguments = ("--", *paths) if paths else ()
    if parent_id is None:
        arguments = (
            "show",
            "--format=",
            "--root",
            "-M",
            "--unified=0",
            "--no-ext-diff",
            "--no-textconv",
            commit_id,
            *path_arguments,
        )
    else:
        arguments = (
            "diff",
            "-M",
            "--unified=0",
            "--no-ext-diff",
            "--no-textconv",
            parent_id,
            commit_id,
            *path_arguments,
        )
    process = subprocess.Popen(  # noqa: S603 - hardened absolute Git command
        _git_command(root, *arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        return "", [], 0, False, "Git diff stream was unavailable"
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    preview_parts: list[str] = []
    retained = 0
    source_chars = 0
    hunks: list[dict[str, int | str]] = []
    complete = True
    error: str | None = None
    try:
        while True:
            if time.monotonic() > deadline:
                complete = False
                error = "Git diff stream timed out"
                process.terminate()
                break
            raw_line = process.stdout.readline(64 * 1024)
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace")
            source_chars += len(line)
            if retained < preview_chars:
                part = line[: preview_chars - retained]
                preview_parts.append(part)
                retained += len(part)
            match = _DIFF_HUNK_RE.match(line)
            if match is not None:
                hunks.append(
                    {
                        "repository_path": repository_path,
                        "old_start": int(match.group(1)),
                        "old_lines": int(match.group(2) or "1"),
                        "new_start": int(match.group(3)),
                        "new_lines": int(match.group(4) or "1"),
                    }
                )
        if process.poll() is None:
            process.wait(timeout=1)
        if complete and process.returncode != 0:
            complete = False
            error = compact_text(process.stderr.read().decode("utf-8", errors="replace"))
            error = error or "could not export commit diff"
    except subprocess.TimeoutExpired:
        complete = False
        error = "Git diff stream timed out"
        process.kill()
    finally:
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.kill()
        process.wait()
    return "".join(preview_parts), hunks, source_chars, complete, error


def _blob_metadata(root: Path, revision: str | None, path: str) -> dict[str, object]:
    result: dict[str, object] = {
        "revision": revision,
        "path": path,
        "available": False,
    }
    if revision is None:
        result["unavailable_reason"] = "no_parent_commit"
        return result
    object_result = _git(
        root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{revision}:{path}",
    )
    if object_result.returncode != 0 or not object_result.stdout.strip():
        result["unavailable_reason"] = "not_present"
        return result
    object_id = object_result.stdout.strip()
    result["object_id"] = object_id
    tree_result = _git_bytes(root, "ls-tree", "-z", revision, "--", path)
    if tree_result.returncode == 0 and tree_result.stdout:
        tree_entry = tree_result.stdout.split(b"\0", maxsplit=1)[0]
        tree_match = _LS_TREE_ENTRY_RE.match(tree_entry)
        if tree_match is not None:
            mode, object_type, tree_object_id = tree_match.groups()
            if tree_object_id.decode("ascii") == object_id:
                result["git_mode"] = mode.decode("ascii")
                result["object_type"] = object_type.decode("ascii")
    type_result = _git(root, "cat-file", "-t", object_id)
    if type_result.returncode != 0 or type_result.stdout.strip() != "blob":
        result["unavailable_reason"] = "not_a_blob"
        return result
    size_result = _git(root, "cat-file", "-s", object_id)
    if size_result.returncode != 0 or not size_result.stdout.strip().isdigit():
        result["unavailable_reason"] = "blob_size_unavailable"
        return result
    result.update(
        available=True,
        size_bytes=int(size_result.stdout.strip()),
        readback=(f"learntrace git-file <project> {revision} {path} --lines <start>:<end>"),
    )
    return result


def _hunk_manifest(
    hunk: dict[str, int | str],
    *,
    parent_id: str | None,
    commit_id: str,
    before_path: str,
    after_path: str,
) -> dict[str, object]:
    old_start = int(hunk["old_start"])
    old_lines = int(hunk["old_lines"])
    new_start = int(hunk["new_start"])
    new_lines = int(hunk["new_lines"])
    old_end = old_start + old_lines - 1 if old_lines else None
    new_end = new_start + new_lines - 1 if new_lines else None
    return {
        "old": {
            "start": old_start,
            "end": old_end,
            "lines": old_lines,
            "readback": (
                f"learntrace git-file <project> {parent_id} {before_path} "
                f"--lines {old_start}:{old_end}"
                if parent_id is not None and old_end is not None
                else None
            ),
        },
        "new": {
            "start": new_start,
            "end": new_end,
            "lines": new_lines,
            "readback": (
                f"learntrace git-file <project> {commit_id} {after_path} "
                f"--lines {new_start}:{new_end}"
                if new_end is not None
                else None
            ),
        },
    }


def _validated_git_root(project_root: Path) -> Path:
    root = repo_root(project_root)
    repository = _git(root, "rev-parse", "--is-inside-work-tree")
    if repository.returncode != 0 or repository.stdout.strip() != "true":
        raise ValueError("project root is not a Git work tree")
    top_level = _git(root, "rev-parse", "--show-toplevel")
    if top_level.returncode != 0 or not top_level.stdout.strip():
        raise ValueError("Git did not report a repository root")
    actual_root = Path(top_level.stdout.strip()).resolve(strict=True)
    if os.path.normcase(str(actual_root)) != os.path.normcase(str(root)):
        raise ValueError("project root is a subdirectory of a different Git work tree")
    return root


# Narrow shared primitives for agent-facing Git navigation. Keeping the runner
# here preserves the parser's existing test seam and avoids duplicate security
# and path-validation logic in the navigation module.
def run_git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _git(root, *arguments)


def run_git_bytes(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return _git_bytes(root, *arguments)


def git_command(root: Path, *arguments: str) -> list[str]:
    """Return the same hardened absolute Git command used by parser calls."""
    return _git_command(root, *arguments)


def git_timeout_seconds() -> int:
    return _GIT_TIMEOUT_SECONDS


def stream_git_text_preview(
    root: Path,
    *arguments: str,
    max_chars: int,
) -> dict[str, object]:
    """Stream a Git command, retaining only a bounded UTF-8 preview.

    The complete stream is still consumed so its digest and source size refer
    to the real output, not merely the preview.  Callers must retain separate
    navigation facts because a preview is never the authoritative evidence.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    process = subprocess.Popen(  # noqa: S603 - hardened absolute Git command
        _git_command(root, *arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        return {
            "available": False,
            "complete": False,
            "unavailable_reason": "Git output stream was unavailable",
            "truncated": False,
        }
    preview = bytearray()
    digest = hashlib.sha256()
    source_bytes = 0
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    complete = True
    error: str | None = None
    try:
        while True:
            if time.monotonic() > deadline:
                complete = False
                error = "Git output stream timed out"
                process.terminate()
                break
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            source_bytes += len(chunk)
            if len(preview) < max_chars:
                preview.extend(chunk[: max_chars - len(preview)])
        if process.poll() is None:
            process.wait(timeout=1)
        if complete and process.returncode != 0:
            complete = False
            error = compact_text(process.stderr.read().decode("utf-8", errors="replace"))
            error = error or "Git command failed"
    except subprocess.TimeoutExpired:
        complete = False
        error = "Git output stream timed out"
        process.kill()
    finally:
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.kill()
        process.wait()
    if not complete:
        return {
            "available": False,
            "complete": False,
            "unavailable_reason": error or "Git command did not complete",
            "preview": preview.decode("utf-8", errors="replace"),
            "preview_bytes": len(preview),
            "source_bytes_read": source_bytes,
            "truncated": True,
        }
    content = preview.decode("utf-8", errors="replace")
    return {
        "available": True,
        "complete": True,
        "content": content,
        "sha256": digest.hexdigest(),
        "source_bytes": source_bytes,
        "written_bytes": len(preview),
        "truncated": source_bytes > len(preview),
    }


def path_is_within(path: Path, root: Path) -> bool:
    return _is_within(path, root)


def safe_repository_path(value: str) -> str:
    return _safe_repository_path(value)


def validated_git_root(project_root: Path) -> Path:
    return _validated_git_root(project_root)


def _export_git_evidence(
    project_root: Path,
    commit: str,
    *,
    paths: tuple[str, ...] = (),
    output_dir: Path | None = None,
    max_chars: int = DEFAULT_EVIDENCE_MAX_CHARS,
) -> GitEvidenceExportResult:
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    root = _validated_git_root(project_root)

    commit_id = _resolve_evidence_commit(root, commit)
    metadata = _git(root, "show", "-s", "--format=%P", commit_id)
    if metadata.returncode != 0:
        raise ValueError(compact_text(metadata.stderr) or "could not read commit parents")
    parent_ids = metadata.stdout.split()
    parent_id = parent_ids[0] if parent_ids else None
    changes, change_error = _file_changes(
        root,
        commit_id,
        merge_first_parent=parent_id if len(parent_ids) > 1 else None,
    )
    if change_error is not None:
        raise ValueError(change_error)

    path_policy = EvidencePathPolicy.load(
        root,
        additional_generated=(output_dir,) if output_dir is not None else (),
    )
    changes, excluded_generated = _filter_evidence_changes(path_policy, changes)
    changes_by_path: dict[str, _GitFileChange] = {}
    for change in changes:
        changes_by_path[change.path] = change
        if change.previous_path is not None:
            changes_by_path[change.previous_path] = change
    requested_paths = tuple(dict.fromkeys(_safe_repository_path(path) for path in paths))
    for requested_path in requested_paths:
        rejection = path_policy.reason(requested_path)
        if rejection is not None:
            raise ValueError(f"evidence path rejected: {rejection}")
    if requested_paths:
        unknown = [path for path in requested_paths if path not in changes_by_path]
        if unknown:
            joined = ", ".join(unknown)
            raise ValueError(f"paths are not changed by commit {commit_id[:12]}: {joined}")
        selected_changes = list(dict.fromkeys(changes_by_path[path] for path in requested_paths))
    else:
        selected_changes = changes
    destination = output_dir or root / ".learntrace" / "evidence" / "git" / commit_id
    destination = destination if destination.is_absolute() else root / destination
    resolved_destination = destination.resolve(strict=False)
    if not _is_within(resolved_destination, root):
        raise ValueError("Git evidence output must stay inside the project root")
    destination.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict[str, object]] = []
    change_records: list[dict[str, object]] = []
    preview_parts: list[str] = []
    preview_length = 0
    source_chars = 0
    manifest_complete = True
    diff_errors: list[dict[str, str]] = []
    all_hunks: list[dict[str, int | str]] = []
    for change in selected_changes:
        before_path = change.previous_path or change.path
        diff_paths = tuple(path for path in (change.previous_path, change.path) if path is not None)
        preview, raw_hunks, path_source_chars, complete, diff_error = _stream_commit_diff(
            root,
            commit_id,
            parent_id,
            diff_paths,
            repository_path=change.path,
            preview_chars=max(max_chars - preview_length, 0),
        )
        preview_parts.append(preview)
        preview_length += len(preview)
        source_chars += path_source_chars
        all_hunks.extend(raw_hunks)
        if not complete:
            manifest_complete = False
        if diff_error is not None:
            diff_errors.append({"path": change.path, "message": diff_error})
        before = (
            _blob_metadata(root, parent_id, before_path)
            if change.status != "A"
            else {
                "revision": parent_id,
                "path": before_path,
                "available": False,
                "unavailable_reason": "file_added",
            }
        )
        after = (
            _blob_metadata(root, commit_id, change.path)
            if change.status != "D"
            else {
                "revision": commit_id,
                "path": change.path,
                "available": False,
                "unavailable_reason": "file_deleted",
            }
        )
        manifest_hunks = [
            _hunk_manifest(
                hunk,
                parent_id=parent_id,
                commit_id=commit_id,
                before_path=before_path,
                after_path=change.path,
            )
            for hunk in raw_hunks
        ]
        before_object = before.get("object_id")
        after_object = after.get("object_id")
        before_mode = before.get("git_mode")
        after_mode = after.get("git_mode")
        mode_only = (
            change.status == "M"
            and isinstance(before_object, str)
            and before_object == after_object
            and isinstance(before_mode, str)
            and isinstance(after_mode, str)
            and before_mode != after_mode
        )
        change_records.append(
            {
                "status": change.status,
                "path": change.path,
                "previous_path": change.previous_path,
                "additions": change.additions,
                "deletions": change.deletions,
                "before": before,
                "after": after,
                "before_mode": before_mode,
                "after_mode": after_mode,
                "mode_only": mode_only,
                "hunks": manifest_hunks,
                "hunk_manifest_complete": complete,
                "hunk_error": diff_error,
            }
        )
        artifacts.extend(
            (
                {
                    "kind": "before",
                    "repository_path": before_path,
                    "content_included": False,
                    "truncated": False,
                    **before,
                },
                {
                    "kind": "after",
                    "repository_path": change.path,
                    "content_included": False,
                    "truncated": False,
                    **after,
                },
            )
        )

    diff_content = "".join(preview_parts)
    diff_metadata = _write_text_artifact(
        destination,
        "diff.patch",
        diff_content,
        max_chars=max_chars,
    )
    diff_metadata.update(
        {
            "kind": "diff",
            "source_revision": commit_id,
            "base_revision": parent_id,
            "repository_paths": [change.path for change in selected_changes],
            "hunks": all_hunks,
            "source_chars": source_chars,
            "written_chars": len(diff_content),
            "truncated": source_chars > len(diff_content) or not manifest_complete,
            "hunk_manifest_complete": manifest_complete,
            "errors": diff_errors,
        }
    )
    artifacts.insert(0, diff_metadata)

    index = {
        "schema_version": "v0",
        "kind": "git_evidence_export",
        "commit_id": commit_id,
        "base_revision": parent_id,
        "requested_paths": list(requested_paths),
        "exported_paths": [change.path for change in selected_changes],
        "evidence_scope": "local_authorized_project",
        "redaction_applied": False,
        "max_chars_per_artifact": max_chars,
        "changes": change_records,
        "hunk_manifest_complete": manifest_complete,
        "excluded_generated_artifacts": {
            "count": len(excluded_generated),
            "paths": excluded_generated,
        },
        "artifacts": artifacts,
    }
    index_path = _artifact_target(destination, "index.json")
    from learntrace.parsers.git_navigation import atomic_write_text

    atomic_write_text(index_path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    register_generated_artifacts(root, (index_path, destination / "diff.patch"))
    return GitEvidenceExportResult(
        commit_id=commit_id,
        output_dir=destination,
        index_path=index_path,
        artifact_count=len(artifacts),
    )


def export_git_evidence(
    project_root: Path,
    commit: str,
    *,
    paths: tuple[str, ...] = (),
    output_dir: Path | None = None,
    max_chars: int = DEFAULT_EVIDENCE_MAX_CHARS,
) -> GitEvidenceExportResult:
    """按需导出一个提交的 diff 与变更前后源码，供宿主 Agent 回读。"""
    try:
        return _export_git_evidence(
            project_root,
            commit,
            paths=paths,
            output_dir=output_dir,
            max_chars=max_chars,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Git evidence export timed out") from error
    except OSError as error:
        raise ValueError(safe_os_error(error)) from error


def _write_git_history_index(
    project_root: Path,
    output_path: Path | None,
) -> GitHistoryIndexResult:
    root = _validated_git_root(project_root)
    entries, history_error = _read_history_index(root)
    if history_error is not None:
        raise ValueError(history_error)
    first_parent_ids, first_parent_error = _first_parent_ids(root)
    if first_parent_error is not None:
        raise ValueError(first_parent_error)
    history_state = _repository_history_state(root)
    path_policy = EvidencePathPolicy.load(
        root,
        additional_generated=(output_path,) if output_path else (),
    )

    # Imported lazily so the navigation module can reuse the hardened Git runner
    # without creating an import cycle during module initialization.
    from learntrace.parsers.git_navigation import atomic_write_text, file_roles
    from learntrace.parsers.git_tree import tree_summary

    commit_records: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    tree_cache: dict[str, tuple[str, int, dict[str, int]]] = {}
    excluded_generated: set[str] = set()
    for entry in entries:
        first_parent = entry.parent_ids[0] if entry.parent_ids else None
        try:
            changes, change_error = _file_changes(
                root,
                entry.commit_id,
                merge_first_parent=first_parent if len(entry.parent_ids) > 1 else None,
            )
        except subprocess.TimeoutExpired:
            changes, change_error = [], "Git file-change read timed out"
        except OSError as error:
            changes, change_error = [], safe_os_error(error)
        changes, excluded = _filter_evidence_changes(path_policy, changes)
        excluded_generated.update(excluded)
        files = [
            {
                "status": change.status,
                "path": change.path,
                "previous_path": change.previous_path,
                "additions": change.additions,
                "deletions": change.deletions,
                "roles": file_roles(change.path),
            }
            for change in changes
        ]
        tree_id: str | None = None
        file_count: int | None = None
        top_level_counts: dict[str, int] | None = None
        tree_error: str | None = None
        try:
            tree_id, file_count, top_level_counts = tree_summary(
                root,
                entry.commit_id,
                cache=tree_cache,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            tree_error = (
                "Git tree read timed out"
                if isinstance(error, subprocess.TimeoutExpired)
                else safe_os_error(error)
                if isinstance(error, OSError)
                else str(error)
            )
            errors.append({"commit_id": entry.commit_id, "message": tree_error})
        source_paths = [change.path for change in changes if "source" in file_roles(change.path)]
        test_paths = [change.path for change in changes if "test" in file_roles(change.path)]
        relations: list[dict[str, object]] = []
        if source_paths and test_paths:
            relations.append(
                {
                    "type": "same_commit",
                    "source_paths": source_paths,
                    "test_paths": test_paths,
                    "causal": False,
                }
            )
        record: dict[str, object] = {
            "record_type": "commit",
            "commit_id": entry.commit_id,
            "occurred_at": entry.occurred_at,
            "parents": list(entry.parent_ids),
            "subject": entry.subject,
            "is_first_parent": entry.commit_id in first_parent_ids,
            "is_merge": len(entry.parent_ids) > 1,
            "tree_id": tree_id,
            "file_count": file_count,
            "top_level_counts": top_level_counts,
            "files_available": change_error is None,
            "files": files,
            "relations": relations,
            "evidence_eligible": bool(changes),
            "excluded_generated_artifacts": {
                "count": len(excluded),
                "paths": excluded,
            },
        }
        if change_error is not None:
            record["file_error"] = change_error
            errors.append({"commit_id": entry.commit_id, "message": change_error})
        if tree_error is not None:
            record["tree_error"] = tree_error
        commit_records.append(record)

    destination = output_path or root / ".learntrace" / "evidence" / "git" / "history.jsonl"
    destination = destination if destination.is_absolute() else root / destination
    resolved_destination = destination.resolve(strict=False)
    if not _is_within(resolved_destination, root):
        raise ValueError("Git history index must stay inside the project root")
    records_complete = not errors
    history_complete = not history_state.shallow
    metadata = {
        "record_type": "history_metadata",
        "head": entries[0].commit_id if entries else None,
        "total_commits": len(entries),
        "repository_shallow": history_state.shallow,
        "shallow_boundary_commits": list(history_state.shallow_boundaries),
        "history_complete": history_complete,
        "records_complete": records_complete,
        "details_truncated": False,
        "complete": history_complete and records_complete,
        "ordering": "topological_newest_first",
        "errors": errors,
        "excluded_generated_artifacts": {
            "count": len(excluded_generated),
            "paths": sorted(excluded_generated),
        },
    }
    lines = [json.dumps(metadata, ensure_ascii=False)]
    lines.extend(json.dumps(record, ensure_ascii=False) for record in commit_records)
    target = _artifact_target(destination.parent, destination.name)
    atomic_write_text(target, "\n".join(lines) + "\n")
    register_generated_artifacts(root, (target,))
    return GitHistoryIndexResult(
        output_path=target,
        total_commits=len(entries),
        complete=history_complete and records_complete,
    )


def write_git_history_index(
    project_root: Path,
    output_path: Path | None = None,
) -> GitHistoryIndexResult:
    """写出完整、逐行可检索的本地 Git 导航索引。"""
    try:
        return _write_git_history_index(project_root, output_path)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Git history index timed out") from error
    except OSError as error:
        raise ValueError(safe_os_error(error)) from error
def read_git_commit_text(project_root: Path, commit_id: str) -> str:
    """读取单个提交的完整文本（提交信息 + diff）；不可读时抛 ValueError。"""
    root = repo_root(project_root)
    result = _git(
        root,
        "show",
        "--no-color",
        "--format=%H%x1f%an <%ae>%x1f%cI%x1f%s",
        commit_id,
    )
    if result.returncode != 0:
        message = compact_text(result.stderr) or "could not read commit"
        raise ValueError(message)
    return result.stdout


def list_git_authors(project_root: Path) -> tuple[GitAuthor, ...]:
    """列出全部历史提交的作者（名称、邮箱、提交数）；不可读或读取失败返回空。"""
    root = repo_root(project_root)
    try:
        repository = _git(root, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if repository.returncode != 0 or repository.stdout.strip() != "true":
        return ()
    try:
        entries = _history_commit_authors(root, warnings=[])
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if entries is None:
        return ()
    counts: dict[tuple[str, str], int] = {}
    for _commit_id, name, email in entries:
        counts[(name, email)] = counts.get((name, email), 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0][0].casefold()))
    return tuple(
        GitAuthor(name=name, email=email, commits=count) for (name, email), count in ranked
    )


def parse_git_history(
    project_root: Path,
    *,
    max_commits: int | None = None,
    find_copies_harder: bool = False,
    excluded_paths: tuple[Path, ...] = (),
    author: str | None = None,
) -> ParseResult:
    """读取 Git 历史；细节预算不会丢弃 first-parent 主线或 merge 边界。

    传入 ``author`` 时按作者名称或邮箱整体、大小写不敏感匹配，
    其余提交在解析层过滤并显式告警。
    """
    root = repo_root(project_root)
    if max_commits is not None and max_commits < 1:
        msg = "max_commits must be at least 1"
        raise ValueError(msg)
    try:
        repository = _git(root, "rev-parse", "--is-inside-work-tree")
    except OSError as error:
        return _warning("git_unavailable", root, safe_os_error(error))
    except subprocess.TimeoutExpired:
        return _warning("git_timeout", root, "Git repository check timed out")
    if repository.returncode != 0 or repository.stdout.strip() != "true":
        return _warning("not_git_repository", root, "project root is not a Git work tree")

    try:
        top_level = _git(root, "rev-parse", "--show-toplevel")
    except OSError as error:
        return _warning("git_read_error", root, safe_os_error(error))
    except subprocess.TimeoutExpired:
        return _warning("git_timeout", root, "Git repository root check timed out")
    if top_level.returncode != 0 or not top_level.stdout.strip():
        message = compact_text(top_level.stderr) or "Git did not report a repository root"
        return _warning("git_read_error", root, message)
    try:
        actual_root = Path(top_level.stdout.strip()).resolve(strict=True)
    except OSError as error:
        return _warning("git_read_error", root, safe_os_error(error))
    if os.path.normcase(str(actual_root)) != os.path.normcase(str(root)):
        return _warning(
            "git_root_mismatch",
            root,
            "project root is a subdirectory of a different Git work tree",
        )

    try:
        head = _git(root, "rev-parse", "--verify", "HEAD")
        if head.returncode != 0:
            return _warning("git_no_commits", root, "Git history has no commits")
        history_entries, history_error = _read_history_index(root)
        first_parent_ids, first_parent_error = _first_parent_ids(root)
        history_state = _repository_history_state(root)
    except OSError as error:
        return _warning("git_read_error", root, safe_os_error(error))
    except subprocess.TimeoutExpired:
        return _warning("git_timeout", root, "Git history read timed out")
    if history_error is not None:
        return _warning("git_read_error", root, history_error)
    if first_parent_error is not None:
        return _warning("git_read_error", root, first_parent_error)
    if not history_entries:
        return _warning("git_no_commits", root, "Git history has no commits")

    events: list[ObservableEvent] = []
    warnings: list[ParseWarning] = []
    path_policy = EvidencePathPolicy.load(root, additional_generated=excluded_paths)
    excluded_generated: set[str] = set()
    if history_state.shallow:
        warnings.append(
            ParseWarning(
                "git_history_shallow",
                ".",
                (
                    "Local Git history is shallow; the earliest visible commit is a local "
                    "boundary, not a verified project beginning"
                ),
                (
                    ("repository_shallow", True),
                    ("history_complete", False),
                    ("shallow_boundary_count", len(history_state.shallow_boundaries)),
                    ("shallow_boundary_commits", ",".join(history_state.shallow_boundaries)),
                ),
            )
        )
    if author is not None:
        try:
            history_authors = _history_commit_authors(root, warnings=warnings)
        except (OSError, subprocess.TimeoutExpired):
            return ParseResult(warnings=tuple(warnings))
        if history_authors is None:
            return ParseResult(warnings=tuple(warnings))
        author_by_hash = {commit_id: (name, email) for commit_id, name, email in history_authors}
        filtered_entries = [
            entry
            for entry in history_entries
            if _author_matches(author, *author_by_hash.get(entry.commit_id, ("", "")))
        ]
        filtered_count = len(history_entries) - len(filtered_entries)
        history_entries = filtered_entries
        if filtered_count > 0:
            warnings.append(
                ParseWarning(
                    "git_author_filtered",
                    ".",
                    f"多作者协作边界：最近提交中有 {filtered_count} 个不属于作者"
                    f"“{author.strip()}”（按名称或邮箱整体、大小写不敏感匹配），"
                    "已在解析层过滤，不进入报告。",
                )
            )
        if not history_entries:
            return ParseResult(events=(), warnings=tuple(warnings))
    if author is not None:
        selected = history_entries if max_commits is None else history_entries[:max_commits]
        omitted = []
        if max_commits is not None and len(history_entries) > max_commits:
            warnings.append(
                ParseWarning(
                    "git_history_truncated",
                    ".",
                    f"Git history limited to the newest {max_commits} matching author commits.",
                )
            )
    else:
        selected, omitted, truncation_details = _select_history(
            history_entries,
            first_parent_ids,
            max_commits,
        )
    if author is None and omitted:
        truncation_details = (
            *truncation_details,
            ("history_index_path", ".learntrace/evidence/git/history.jsonl"),
            ("history_index_status", "requires_generation"),
            ("history_index_command", "learntrace git-index <project>"),
            ("history_index_expected_head", head.stdout.strip()),
        )
        detail_values = dict(truncation_details)
        warnings.append(
            ParseWarning(
                "git_history_truncated",
                ".",
                (
                    f"Git history retained {detail_values['retained_commits']} commits, including "
                    f"the complete first-parent chain and all merge commits; "
                    f"{detail_values['omitted_commits']} side-branch commits were aggregated. "
                    "The complete history index was not generated by this parse; run "
                    "learntrace git-index <project> before reading it"
                ),
                truncation_details,
            )
        )
    for entry in selected:
        commit_id = entry.commit_id
        try:
            metadata = _git(root, "show", "-s", "--format=%H%x1f%cI%x1f%P%x1f%s", commit_id)
        except OSError as error:
            warnings.append(ParseWarning("git_commit_read_error", commit_id, safe_os_error(error)))
            continue
        except subprocess.TimeoutExpired:
            warnings.append(ParseWarning("git_commit_timeout", commit_id, "Git read timed out"))
            continue
        if metadata.returncode != 0:
            message = compact_text(metadata.stderr) or "could not read commit"
            warnings.append(ParseWarning("git_commit_read_error", commit_id, message))
            continue
        fields = metadata.stdout.strip().split(_FIELD_SEPARATOR, maxsplit=3)
        if len(fields) != 4:
            warnings.append(
                ParseWarning("git_commit_format_error", commit_id, "unexpected Git metadata format")
            )
            continue
        full_hash, occurred_at, parent_text, subject = fields
        parent_ids = parent_text.split()
        merge_first_parent = parent_ids[0] if len(parent_ids) > 1 else None
        try:
            changes, change_error = _file_changes(
                root,
                commit_id,
                merge_first_parent=merge_first_parent,
                find_copies_harder=find_copies_harder,
            )
        except OSError as error:
            warnings.append(ParseWarning("git_commit_read_error", commit_id, safe_os_error(error)))
            continue
        except subprocess.TimeoutExpired:
            warnings.append(ParseWarning("git_commit_timeout", commit_id, "Git read timed out"))
            continue
        if change_error is not None:
            warnings.append(ParseWarning("git_commit_read_error", commit_id, change_error))
            continue
        changes, excluded = _filter_evidence_changes(path_policy, changes)
        if not changes and excluded:
            continue
        subject_text = compact_text(subject) or "（提交信息未记录）"
        if not changes:
            events.append(
                ObservableEvent(
                    id=f"evt-git-{full_hash}",
                    kind=EventKind.GIT_COMMIT,
                    summary=f"提交 {full_hash[:7]} 的提交信息为“{subject_text}”，记录 0 个文件变更（0 个）。",
                    source_refs=(_commit_source_ref(full_hash),),
                    occurred_at=occurred_at,
                )
            )
            continue
        subject_text = compact_text(subject) or "（提交信息未记录）"
        summary = (
            f"提交 {full_hash[:7]} 的提交信息为“{subject_text}”，"
            f"记录 {len(changes)} 个文件变更（{_change_counts(changes)}）。"
        )
        source_refs = [_commit_source_ref(full_hash)]
        for change in changes:
            source_refs.append(_file_source_ref(change.path))
        events.append(
            ObservableEvent(
                id=f"evt-git-{full_hash}",
                kind=EventKind.GIT_COMMIT,
                summary=summary,
                source_refs=tuple(source_refs),
                occurred_at=occurred_at,
            )
        )
        for change in changes:
            change_refs = [
                _commit_source_ref(full_hash),
                _file_source_ref(change.path),
            ]
            if change.previous_path is not None:
                change_refs.append(
                    _file_source_ref(
                        change.previous_path,
                        note="变更前路径",
                    )
                )
            events.append(
                ObservableEvent(
                    id=stable_event_id(
                        "git-file",
                        full_hash,
                        change.status,
                        change.path,
                        change.previous_path or "",
                    ),
                    kind=EventKind.GIT_COMMIT,
                    summary=_change_summary(full_hash, change),
                    source_refs=tuple(change_refs),
                    occurred_at=occurred_at,
                )
            )
    if omitted:
        events.append(_history_placeholder(head.stdout.strip(), omitted))
    if excluded_generated:
        warnings.append(
            ParseWarning(
                "git_generated_artifacts_excluded",
                ".",
                "LearnTrace-generated files were excluded from Git evidence",
                (
                    ("count", len(excluded_generated)),
                    ("paths", ",".join(sorted(excluded_generated))),
                ),
            )
        )
    return ParseResult(events=tuple(events), warnings=tuple(warnings))
