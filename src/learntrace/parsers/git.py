"""Git 元数据的确定性只读解析。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from learntrace.models import EventKind, ObservableEvent, SourceRef, SourceType
from learntrace.parsers._common import compact_text, repo_root
from learntrace.parsers.types import ParseResult, ParseWarning

_FIELD_SEPARATOR = "\x1f"


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    command = [
        "git",
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
    )


def _warning(code: str, root: Path, message: str) -> ParseResult:
    return ParseResult(warnings=(ParseWarning(code, root.as_posix(), message),))


def parse_git_history(project_root: Path, *, max_commits: int = 50) -> ParseResult:
    """读取最近 Git 提交；不运行钩子、diff 驱动或用户项目命令。"""
    root = repo_root(project_root)
    if max_commits < 1:
        msg = "max_commits must be at least 1"
        raise ValueError(msg)
    try:
        repository = _git(root, "rev-parse", "--is-inside-work-tree")
    except OSError as error:
        return _warning("git_unavailable", root, str(error))
    if repository.returncode != 0 or repository.stdout.strip() != "true":
        return _warning("not_git_repository", root, "project root is not a Git work tree")

    revisions = _git(root, "rev-list", f"--max-count={max_commits}", "HEAD")
    if revisions.returncode != 0:
        stderr = compact_text(revisions.stderr) or "Git history has no commits"
        code = "git_no_commits" if "unknown revision" in stderr.lower() else "git_read_error"
        return _warning(code, root, stderr)

    commit_ids = [line.strip() for line in revisions.stdout.splitlines() if line.strip()]
    if not commit_ids:
        return _warning("git_no_commits", root, "Git history has no commits")

    events: list[ObservableEvent] = []
    warnings: list[ParseWarning] = []
    for commit_id in commit_ids:
        metadata = _git(root, "show", "-s", "--format=%H%x1f%cI%x1f%s", commit_id)
        files = _git(
            root,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--no-renames",
            "--name-only",
            "-r",
            "-z",
            commit_id,
        )
        if metadata.returncode != 0 or files.returncode != 0:
            message = compact_text(metadata.stderr or files.stderr) or "could not read commit"
            warnings.append(ParseWarning("git_commit_read_error", commit_id, message))
            continue
        fields = metadata.stdout.strip().split(_FIELD_SEPARATOR, maxsplit=2)
        if len(fields) != 3:
            warnings.append(
                ParseWarning("git_commit_format_error", commit_id, "unexpected Git metadata format")
            )
            continue
        full_hash, occurred_at, subject = fields
        changed_files = sorted(
            {Path(value).as_posix() for value in files.stdout.split("\0") if value.strip()}
        )
        subject_text = compact_text(subject, limit=180) or "（提交信息未记录）"
        summary = (
            f"提交 {full_hash[:7]} 的提交信息为“{subject_text}”，"
            f"记录 {len(changed_files)} 个修改文件。"
        )
        source_refs = [SourceRef(type=SourceType.GIT_COMMIT, ref=full_hash)]
        source_refs.extend(SourceRef(type=SourceType.FILE, ref=path) for path in changed_files)
        events.append(
            ObservableEvent(
                id=f"evt-git-{full_hash}",
                kind=EventKind.GIT_COMMIT,
                summary=summary,
                source_refs=tuple(source_refs),
                occurred_at=occurred_at,
            )
        )
    return ParseResult(events=tuple(events), warnings=tuple(warnings))
