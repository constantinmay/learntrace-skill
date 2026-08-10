"""Git 元数据的确定性只读解析。"""

from __future__ import annotations

import errno
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.parsers._common import compact_text, repo_root, safe_os_error, stable_event_id
from learntrace.parsers.types import ParseResult, ParseWarning

_FIELD_SEPARATOR = "\x1f"
_GIT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True, slots=True)
class _GitFileChange:
    status: str
    path: str
    previous_path: str | None
    additions: int | None
    deletions: int | None


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


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    command = [
        str(_resolve_git_executable(root)),
        "-C",
        str(root),
        "-c",
        "core.quotepath=false",
        "--no-pager",
        *arguments,
    ]
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
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


def parse_git_history(
    project_root: Path,
    *,
    max_commits: int = 50,
    find_copies_harder: bool = False,
) -> ParseResult:
    """读取最近 Git 提交；不运行钩子、diff 驱动或用户项目命令。"""
    root = repo_root(project_root)
    if max_commits < 1:
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
        revisions = _git(root, "rev-list", f"--max-count={max_commits + 1}", "HEAD")
    except OSError as error:
        return _warning("git_read_error", root, safe_os_error(error))
    except subprocess.TimeoutExpired:
        return _warning("git_timeout", root, "Git history read timed out")
    if revisions.returncode != 0:
        stderr = compact_text(revisions.stderr) or "Git history has no commits"
        code = "git_no_commits" if "unknown revision" in stderr.lower() else "git_read_error"
        return _warning(code, root, stderr)

    commit_ids = [line.strip() for line in revisions.stdout.splitlines() if line.strip()]
    if not commit_ids:
        return _warning("git_no_commits", root, "Git history has no commits")

    events: list[ObservableEvent] = []
    warnings: list[ParseWarning] = []
    if len(commit_ids) > max_commits:
        warnings.append(
            ParseWarning(
                "git_history_truncated",
                ".",
                f"Git history was limited to the newest {max_commits} commits",
            )
        )
        commit_ids = commit_ids[:max_commits]
    for commit_id in commit_ids:
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
        subject_text = compact_text(subject) or "（提交信息未记录）"
        summary = (
            f"提交 {full_hash[:7]} 的提交信息为“{subject_text}”，"
            f"记录 {len(changes)} 个文件变更（{_change_counts(changes)}）。"
        )
        source_refs = [SourceRef(type=SourceType.GIT_COMMIT, ref=full_hash)]
        source_refs.extend(SourceRef(type=SourceType.FILE, ref=change.path) for change in changes)
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
                SourceRef(type=SourceType.GIT_COMMIT, ref=full_hash),
                SourceRef(type=SourceType.FILE, ref=change.path),
            ]
            if change.previous_path is not None:
                change_refs.append(
                    SourceRef(
                        type=SourceType.FILE,
                        ref=change.previous_path,
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
    return ParseResult(events=tuple(events), warnings=tuple(warnings))
