"""Git 元数据的确定性只读解析。"""

from __future__ import annotations

import errno
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def _select_history(
    entries: list[_GitHistoryEntry],
    first_parent_ids: set[str],
    max_commits: int | None,
) -> tuple[list[_GitHistoryEntry], list[_GitHistoryEntry], tuple[tuple[str, int | bool], ...]]:
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
    details: tuple[tuple[str, int | bool], ...] = (
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
    sampled = omitted[:3]
    source_refs = tuple(_commit_source_ref(entry.commit_id) for entry in sampled)
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


def _diff_hunks(content: str, repository_path: str) -> list[dict[str, int | str]]:
    hunks: list[dict[str, int | str]] = []
    for match in _DIFF_HUNK_RE.finditer(content):
        hunks.append(
            {
                "repository_path": repository_path,
                "old_start": int(match.group(1)),
                "old_lines": int(match.group(2) or "1"),
                "new_start": int(match.group(3)),
                "new_lines": int(match.group(4) or "1"),
            }
        )
    return hunks


def _hunk_ranges(
    hunks: list[dict[str, int | str]],
    *,
    side: str,
    artifact_path: str,
) -> list[dict[str, int | str]]:
    start_key = f"{side}_start"
    lines_key = f"{side}_lines"
    ranges: list[dict[str, int | str]] = []
    for hunk in hunks:
        start = hunk[start_key]
        line_count = hunk[lines_key]
        if not isinstance(start, int) or not isinstance(line_count, int) or line_count <= 0:
            continue
        end = start + line_count - 1
        ranges.append(
            {
                "start": start,
                "end": end,
                "lines": line_count,
                "ref": f"{artifact_path}:{start}-{end}",
            }
        )
    return ranges


def _read_commit_diff(
    root: Path,
    commit_id: str,
    parent_id: str | None,
    paths: tuple[str, ...],
) -> str:
    path_arguments = ("--", *paths) if paths else ()
    if parent_id is None:
        result = _git(
            root,
            "show",
            "--format=",
            "--root",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            commit_id,
            *path_arguments,
        )
    else:
        result = _git(
            root,
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            parent_id,
            commit_id,
            *path_arguments,
        )
    if result.returncode != 0:
        message = compact_text(result.stderr) or "could not export commit diff"
        raise ValueError(message)
    return result.stdout


def _read_blob(root: Path, revision: str, path: str) -> tuple[str | None, str | None]:
    result = _git_bytes(root, "show", f"{revision}:{path}")
    if result.returncode != 0:
        return None, "not_present"
    if b"\0" in result.stdout:
        return None, "binary"
    try:
        return result.stdout.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "non_utf8"


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

    changes_by_path: dict[str, _GitFileChange] = {}
    for change in changes:
        changes_by_path[change.path] = change
        if change.previous_path is not None:
            changes_by_path[change.previous_path] = change
    requested_paths = tuple(dict.fromkeys(_safe_repository_path(path) for path in paths))
    if requested_paths:
        unknown = [path for path in requested_paths if path not in changes_by_path]
        if unknown:
            joined = ", ".join(unknown)
            raise ValueError(f"paths are not changed by commit {commit_id[:12]}: {joined}")
        selected_changes = list(dict.fromkeys(changes_by_path[path] for path in requested_paths))
    else:
        selected_changes = changes
    diff_paths = tuple(
        dict.fromkeys(
            path
            for change in selected_changes
            for path in (change.previous_path, change.path)
            if path is not None
        )
    )

    destination = output_dir or root / ".learntrace" / "evidence" / "git" / commit_id
    destination = destination if destination.is_absolute() else root / destination
    resolved_destination = destination.resolve(strict=False)
    if not _is_within(resolved_destination, root):
        raise ValueError("Git evidence output must stay inside the project root")
    destination.mkdir(parents=True, exist_ok=True)

    artifacts: list[dict[str, object]] = []
    diff_content = _read_commit_diff(root, commit_id, parent_id, diff_paths)
    hunks_by_path = {
        change.path: _diff_hunks(
            _read_commit_diff(
                root,
                commit_id,
                parent_id,
                tuple(path for path in (change.previous_path, change.path) if path is not None),
            ),
            change.path,
        )
        for change in selected_changes
    }
    hunks = [hunk for path_hunks in hunks_by_path.values() for hunk in path_hunks]
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
            "repository_paths": list(diff_paths),
            "hunks": hunks,
        }
    )
    artifacts.append(diff_metadata)

    for change in selected_changes:
        artifact: dict[str, object]
        change_hunks = hunks_by_path[change.path]
        before_path = change.previous_path or change.path
        if parent_id is not None and change.status != "A":
            before, reason = _read_blob(root, parent_id, before_path)
            if before is not None:
                before_artifact_path = f"before/{before_path}"
                artifact = _write_text_artifact(
                    destination,
                    before_artifact_path,
                    before,
                    max_chars=max_chars,
                )
                artifact.update(
                    {
                        "kind": "before",
                        "source_revision": parent_id,
                        "repository_path": before_path,
                        "relevant_ranges": _hunk_ranges(
                            change_hunks,
                            side="old",
                            artifact_path=before_artifact_path,
                        ),
                    }
                )
            else:
                artifact = {
                    "kind": "before",
                    "source_revision": parent_id,
                    "repository_path": before_path,
                    "available": False,
                    "reason": reason,
                    "truncated": False,
                }
            artifacts.append(artifact)
        else:
            artifacts.append(
                {
                    "kind": "before",
                    "source_revision": parent_id,
                    "repository_path": before_path,
                    "available": False,
                    "reason": "file_added" if change.status == "A" else "no_parent_commit",
                    "truncated": False,
                }
            )
        if change.status != "D":
            after, reason = _read_blob(root, commit_id, change.path)
            if after is not None:
                after_artifact_path = f"after/{change.path}"
                artifact = _write_text_artifact(
                    destination,
                    after_artifact_path,
                    after,
                    max_chars=max_chars,
                )
                artifact.update(
                    {
                        "kind": "after",
                        "source_revision": commit_id,
                        "repository_path": change.path,
                        "relevant_ranges": _hunk_ranges(
                            change_hunks,
                            side="new",
                            artifact_path=after_artifact_path,
                        ),
                    }
                )
            else:
                artifact = {
                    "kind": "after",
                    "source_revision": commit_id,
                    "repository_path": change.path,
                    "available": False,
                    "reason": reason,
                    "truncated": False,
                }
            artifacts.append(artifact)
        else:
            artifacts.append(
                {
                    "kind": "after",
                    "source_revision": commit_id,
                    "repository_path": change.path,
                    "available": False,
                    "reason": "file_deleted",
                    "truncated": False,
                }
            )

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
        "artifacts": artifacts,
    }
    index_path = _artifact_target(destination, "index.json")
    from learntrace.parsers.git_navigation import atomic_write_text

    atomic_write_text(index_path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
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

    # Imported lazily so the navigation module can reuse the hardened Git runner
    # without creating an import cycle during module initialization.
    from learntrace.parsers.git_navigation import atomic_write_text, file_roles
    from learntrace.parsers.git_tree import tree_summary

    commit_records: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    tree_cache: dict[str, tuple[str, int, dict[str, int]]] = {}
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
    metadata = {
        "record_type": "history_metadata",
        "head": entries[0].commit_id if entries else None,
        "total_commits": len(entries),
        "complete": not errors,
        "ordering": "topological_newest_first",
        "errors": errors,
    }
    lines = [json.dumps(metadata, ensure_ascii=False)]
    lines.extend(json.dumps(record, ensure_ascii=False) for record in commit_records)
    target = _artifact_target(destination.parent, destination.name)
    atomic_write_text(target, "\n".join(lines) + "\n")
    return GitHistoryIndexResult(
        output_path=target,
        total_commits=len(entries),
        complete=not errors,
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


def parse_git_history(
    project_root: Path,
    *,
    max_commits: int | None = None,
    find_copies_harder: bool = False,
) -> ParseResult:
    """读取 Git 历史；细节预算不会丢弃 first-parent 主线或 merge 边界。"""
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
    selected, omitted, truncation_details = _select_history(
        history_entries,
        first_parent_ids,
        max_commits,
    )
    if omitted:
        detail_values = dict(truncation_details)
        warnings.append(
            ParseWarning(
                "git_history_truncated",
                ".",
                (
                    f"Git history retained {detail_values['retained_commits']} commits, including "
                    f"the complete first-parent chain and all merge commits; "
                    f"{detail_values['omitted_commits']} side-branch commits were aggregated"
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
    return ParseResult(events=tuple(events), warnings=tuple(warnings))
