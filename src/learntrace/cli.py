"""Unified command-line entry point for the LearnTrace local pipeline."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from learntrace import __version__
from learntrace.adapters import (
    TraceAdapterResult,
    TraceEventMetadata,
    TraceInputStatus,
    TraceParseIssue,
    adapt_claude_code_exports,
    adapt_codex_exports,
    adapt_opencode_exports,
    canonical_trace_event,
    events_conflict,
    segment_trace_events,
    write_trace_result,
)
from learntrace.archive import main as archive_main
from learntrace.archive import write_learning_record_result
from learntrace.artifacts import register_generated_artifacts
from learntrace.evidence import (
    export_git_commit,
    export_project_file,
    export_session_evidence,
    load_evidence_index,
    record_full_read_authorization,
)
from learntrace.models import ObservableEvent
from learntrace.parsers import (
    DEFAULT_EVIDENCE_MAX_CHARS,
    discover_static_materials,
    export_git_evidence,
    parse_static_materials,
    write_git_file,
    write_git_history_index,
    write_git_tree,
    write_git_worktree,
    write_parse_result,
)
from learntrace.reporting import (
    load_archive,
    load_payload,
    render_narrative_markdown,
    verify_payload,
)

_COMMANDS = frozenset(
    {
        "adapt",
        "archive",
        "discover",
        "git-evidence",
        "git-file",
        "git-index",
        "git-tree",
        "git-worktree",
        "authorize-full-read",
        "export-evidence",
        "parse",
        "render-narrative",
        "run",
        "verify-narrative",
    }
)
_MAX_TOTAL_TRACE_EVENTS = 10_000
_TRACE_EXPORT_ADAPTERS = {
    "opencode": adapt_opencode_exports,
    "claude-code": adapt_claude_code_exports,
    "codex": adapt_codex_exports,
}


def _add_parse_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_dir", type=Path, help="Project repository to inspect.")
    parser.add_argument("--document", action="append", type=Path, default=None)
    parser.add_argument("--test-log", action="append", type=Path, default=None)
    parser.add_argument("--no-git", action="store_true", help="Do not read local Git history.")
    parser.add_argument(
        "--max-commits",
        type=int,
        default=None,
        help=(
            "Optional side-branch detail budget; by default the complete reachable history is "
            "retained. The first-parent chain and merge commits are never removed."
        ),
    )
    parser.add_argument("--find-copies-harder", action="store_true")
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help=(
            "Multi-author repositories: keep only this author's commits (matched "
            "literally, case-insensitively, against the author name or email). "
            "Other authors' commits are filtered at the parse layer and reported "
            "as an explicit collaboration boundary, not hidden."
        ),
    )


def _line_range(value: str) -> tuple[int, int]:
    match = value.split(":", maxsplit=1)
    if len(match) != 2:
        raise argparse.ArgumentTypeError("line range must use START:END")
    try:
        start, end = (int(part) for part in match)
    except ValueError as error:
        raise argparse.ArgumentTypeError("line range must contain integers") from error
    if start < 1 or end < start:
        raise argparse.ArgumentTypeError("line range must satisfy 1 <= START <= END")
    return start, end


def _byte_range(value: str) -> tuple[int, int]:
    match = value.split(":", maxsplit=1)
    if len(match) != 2:
        raise argparse.ArgumentTypeError("byte range must use START:END")
    try:
        start, end = (int(part) for part in match)
    except ValueError as error:
        raise argparse.ArgumentTypeError("byte range must contain integers") from error
    if start < 0 or end <= start:
        raise argparse.ArgumentTypeError("byte range must satisfy 0 <= START < END")
    return start, end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learntrace",
        description="Parse local evidence, adapt authorized traces, and build a learning record.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser("parse", help="Produce Task 2 events JSON.")
    _add_parse_options(parse_parser)
    parse_parser.add_argument("-o", "--output", type=Path)

    discover_parser = subparsers.add_parser(
        "discover",
        help="List candidate evidence paths without reading file contents.",
    )
    discover_parser.add_argument("project_dir", type=Path)

    evidence_parser = subparsers.add_parser(
        "git-evidence",
        help="Export a commit hunk manifest, bounded diff preview, and source readback locators.",
    )
    evidence_parser.add_argument("project_dir", type=Path)
    evidence_parser.add_argument("commit", help="Reachable hexadecimal commit id.")
    evidence_parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Changed repository path to export (repeatable; default: every changed path).",
    )
    evidence_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_EVIDENCE_MAX_CHARS,
        help="Maximum preview characters; the complete hunk manifest remains in index.json.",
    )
    evidence_parser.add_argument("-o", "--output-dir", type=Path)

    index_parser = subparsers.add_parser(
        "git-index",
        help="Write a complete local JSONL index of reachable Git history.",
    )
    index_parser.add_argument("project_dir", type=Path)
    index_parser.add_argument("-o", "--output", type=Path)

    tree_parser = subparsers.add_parser(
        "git-tree",
        help="Write the complete tracked file tree for a reachable local revision.",
    )
    tree_parser.add_argument("project_dir", type=Path)
    tree_parser.add_argument("revision")
    tree_parser.add_argument("-o", "--output", type=Path)

    file_parser = subparsers.add_parser(
        "git-file",
        help="Read a bounded source range from a revision or the current worktree.",
    )
    file_parser.add_argument("project_dir", type=Path)
    file_parser.add_argument(
        "revision", help="Reachable revision or the literal 'index' or 'worktree'."
    )
    file_parser.add_argument("path", help="Repository-relative file path.")
    file_range = file_parser.add_mutually_exclusive_group()
    file_range.add_argument("--lines", type=_line_range, metavar="START:END")
    file_range.add_argument("--bytes", type=_byte_range, metavar="START:END")
    file_parser.add_argument("-o", "--output", type=Path)

    worktree_parser = subparsers.add_parser(
        "git-worktree",
        help="Write staged, unstaged, untracked, rename, deletion, and conflict facts.",
    )
    worktree_parser.add_argument("project_dir", type=Path)
    worktree_parser.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_EVIDENCE_MAX_CHARS,
        help="Maximum characters retained for each worktree diff.",
    )
    worktree_parser.add_argument("-o", "--output", type=Path)

    adapt_parser = subparsers.add_parser(
        "adapt",
        help="Adapt an authorized agent trace export (OpenCode, Claude Code, or Codex).",
    )
    adapt_parser.add_argument("export_path", nargs="+", type=Path)
    adapt_parser.add_argument(
        "--source",
        choices=tuple(_TRACE_EXPORT_ADAPTERS),
        default="opencode",
        help="Which agent host the exports come from (default: opencode).",
    )
    adapt_parser.add_argument("--project-root", type=Path)
    adapt_parser.add_argument(
        "--authorized",
        action="store_true",
        help="Authorize the single supplied export (legacy single-session form).",
    )
    adapt_parser.add_argument(
        "--authorize-export",
        action="append",
        type=Path,
        default=[],
        help="Authorize one exact export path (repeat for multiple sessions).",
    )
    adapt_parser.add_argument("-o", "--output", type=Path)

    verify_parser = subparsers.add_parser(
        "verify-narrative",
        help="Check a narrative payload against its archive (three red lines).",
    )
    verify_parser.add_argument("payload_path", type=Path, help="Narrative payload JSON file.")
    verify_parser.add_argument(
        "archive_path", type=Path, help="archive-records.json produced by the archive step."
    )

    export_parser = subparsers.add_parser(
        "export-evidence",
        help="On-demand evidence read-back into .learntrace/evidence/ (not pre-generated).",
    )
    export_parser.add_argument("project_dir", type=Path)
    export_parser.add_argument(
        "--git-commit",
        type=str,
        default=None,
        help="Export one commit (message + diff) to evidence/git/.",
    )
    export_parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Export one project-relative file (redacted) to evidence/files/.",
    )
    export_parser.add_argument(
        "--session-export",
        type=Path,
        default=None,
        help="Export one authorized session slice to evidence/sessions/.",
    )
    export_parser.add_argument(
        "--source",
        choices=tuple(_TRACE_EXPORT_ADAPTERS),
        default="opencode",
        help="Host for --session-export (default: opencode).",
    )
    export_parser.add_argument(
        "--authorization",
        choices=("minimal", "full"),
        default="minimal",
        help=(
            "Session slice retention level: minimal = only the five v0 event "
            "fields; full = the raw file (requires full-read authorization)."
        ),
    )
    export_parser.add_argument("--list", action="store_true", help="Print the evidence index.")

    authorize_parser = subparsers.add_parser(
        "authorize-full-read",
        help=(
            "Record a per-file full-read authorization for one session export, "
            "after the student's explicit consent (required before "
            "export-evidence --authorization full)."
        ),
    )
    authorize_parser.add_argument("project_dir", type=Path)
    authorize_parser.add_argument(
        "--session-export",
        type=Path,
        required=True,
        help="The exact session export file to authorize a full read of.",
    )
    authorize_parser.add_argument(
        "--source",
        choices=tuple(_TRACE_EXPORT_ADAPTERS),
        required=True,
        help="Host for the session export (opencode / claude-code / codex).",
    )
    authorize_parser.add_argument(
        "--authorized-at",
        type=str,
        required=True,
        help="ISO-8601 timestamp of the student's explicit full-read consent.",
    )

    render_parser = subparsers.add_parser(
        "render-narrative",
        help="Render a verified narrative payload to Markdown (working or submitted).",
    )
    render_parser.add_argument("payload_path", type=Path, help="Narrative payload JSON file.")
    render_parser.add_argument(
        "archive_path", type=Path, help="archive-records.json produced by the archive step."
    )
    render_parser.add_argument("-o", "--output", type=Path)
    render_parser.add_argument(
        "--variant",
        choices=("working", "submitted"),
        default=None,
        help="Override the payload's own variant (default: payload.variant).",
    )

    run_parser = subparsers.add_parser("run", help="Run the local parse-to-archive pipeline.")
    _add_parse_options(run_parser)
    run_parser.add_argument("--opencode-export", action="append", type=Path, default=[])
    run_parser.add_argument(
        "--authorized",
        action="store_true",
        help="Authorize the single supplied export (legacy single-session form).",
    )
    run_parser.add_argument(
        "--authorize-opencode-export",
        action="append",
        type=Path,
        default=[],
        help="Authorize one exact OpenCode export path (repeat per session).",
    )
    run_parser.add_argument(
        "--claude-code-export",
        action="append",
        type=Path,
        default=[],
        help="Claude Code session JSONL file (repeat per session).",
    )
    run_parser.add_argument(
        "--authorize-claude-code-export",
        action="append",
        type=Path,
        default=[],
        help="Authorize one exact Claude Code session path (repeat per session).",
    )
    run_parser.add_argument(
        "--codex-export",
        action="append",
        type=Path,
        default=[],
        help="Codex session JSONL file (repeat per session).",
    )
    run_parser.add_argument(
        "--authorize-codex-export",
        action="append",
        type=Path,
        default=[],
        help="Authorize one exact Codex session path (repeat per session).",
    )
    run_parser.add_argument(
        "--confirmations",
        action="append",
        type=Path,
        default=[],
        help=(
            "Second-stage confirmation JSON file (repeatable). This reuses the existing "
            "archive snapshot and cannot be combined with evidence collection options."
        ),
    )
    run_parser.add_argument("-o", "--output", type=Path)
    return parser


def _parse_project(
    args: argparse.Namespace,
    output_path: Path,
    *,
    excluded_documents: tuple[Path, ...] = (),
) -> tuple[int, int]:
    root = args.project_dir.resolve()
    exclusions = (*excluded_documents, output_path)
    discovered = discover_static_materials(root, excluded_paths=exclusions)
    documents = discovered.documents if args.document is None else tuple(args.document)
    excluded = {(path if path.is_absolute() else root / path).resolve() for path in exclusions}
    documents = tuple(path for path in documents if (root / path).resolve() not in excluded)
    test_logs = discovered.test_logs if args.test_log is None else tuple(args.test_log)
    result = parse_static_materials(
        root,
        document_paths=documents,
        test_log_paths=test_logs,
        include_git=not args.no_git,
        max_commits=args.max_commits,
        find_copies_harder=args.find_copies_harder,
        inventory_excluded_paths=exclusions,
        git_author=args.author,
    )
    write_parse_result(result, output_path)
    register_generated_artifacts(root, (output_path,))
    return len(result.events), len(result.warnings)


def _trace_event_sort_key(event: ObservableEvent) -> tuple[bool, str, str]:
    return (
        event.occurred_at is None,
        event.occurred_at or "",
        event.source_refs[0].ref,
    )


def _merge_trace_results(
    results: tuple[tuple[str, TraceAdapterResult], ...],
) -> TraceAdapterResult:
    """Merge per-host adapter results into one Task 3 batch.

    Events are deduplicated by stable ID; conflicting duplicates fail loudly.
    Hosts whose input was supplied but not processed (unauthorized or missing)
    always produce an explicit warning, so partial success across hosts is
    never reported as a fully parsed batch. When more than one host
    contributes input, warning locations are prefixed with the host name so
    warnings stay attributable after merging.
    """

    contributing_hosts = [
        host for host, result in results if result.status is not TraceInputStatus.NOT_PROVIDED
    ]
    multi_host = len(contributing_hosts) > 1
    events_by_id: dict[str, ObservableEvent] = {}
    metadata_by_id: dict[str, TraceEventMetadata] = {}
    warnings: list[TraceParseIssue] = []
    statuses: list[TraceInputStatus] = []
    for host, result in results:
        statuses.append(result.status)
        for issue in result.warnings:
            if multi_host:
                warnings.append(
                    TraceParseIssue(
                        code=issue.code,
                        location=f"{host}.{issue.location}",
                        message=issue.message,
                    )
                )
            else:
                warnings.append(issue)
        if result.status is TraceInputStatus.NOT_AUTHORIZED:
            warnings.append(
                TraceParseIssue(
                    code="host_input_not_authorized",
                    location=host,
                    message=f"宿主 {host} 的会话文件未获授权，未读取。",
                )
            )
        elif result.status is TraceInputStatus.AUTHORIZED_NOT_FOUND:
            warnings.append(
                TraceParseIssue(
                    code="host_authorized_not_found",
                    location=host,
                    message=(
                        f"宿主 {host} 的授权输入未产生可导入事件；"
                        "文件可能不存在，或其中没有受支持的轨迹记录。"
                    ),
                )
            )
        for event in result.events:
            existing = events_by_id.get(event.id)
            if existing is None:
                events_by_id[event.id] = event
            elif events_conflict(existing, event):
                raise ValueError("多个轨迹来源包含 ID 相同但内容冲突的工具记录。")
            else:
                events_by_id[event.id] = canonical_trace_event(existing, event)
        for item in result.trace_metadata:
            existing_metadata = metadata_by_id.get(item.event_id)
            if existing_metadata is not None and existing_metadata != item:
                raise ValueError("多个轨迹来源包含 ID 相同但元数据冲突的工具记录。")
            metadata_by_id[item.event_id] = item

    events = sorted(events_by_id.values(), key=_trace_event_sort_key)
    skipped = len(events) - _MAX_TOTAL_TRACE_EVENTS
    if skipped > 0:
        events = events[:_MAX_TOTAL_TRACE_EVENTS]
        warnings.append(
            TraceParseIssue(
                code="total_event_cap_reached",
                location="trace_merge",
                message=(
                    f"合并后轨迹事件超过 {_MAX_TOTAL_TRACE_EVENTS} 条上限，"
                    f"已保留最早的 {len(events)} 条。"
                ),
            )
        )
    warnings.sort(key=lambda issue: (issue.location, issue.code, issue.message))
    metadata = tuple(metadata_by_id[event.id] for event in events)
    if TraceInputStatus.PARSED in statuses and events:
        status = TraceInputStatus.PARSED
    elif TraceInputStatus.AUTHORIZED_NOT_FOUND in statuses:
        status = TraceInputStatus.AUTHORIZED_NOT_FOUND
    elif TraceInputStatus.NOT_AUTHORIZED in statuses:
        status = TraceInputStatus.NOT_AUTHORIZED
    else:
        status = TraceInputStatus.NOT_PROVIDED
    return TraceAdapterResult(
        status=status,
        events=tuple(events),
        warnings=tuple(warnings),
        work_segments=segment_trace_events(tuple(events), metadata=metadata),
        trace_metadata=metadata,
    )


def _run_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        if args.command == "discover":
            discovered = discover_static_materials(args.project_dir.resolve())
            inventory = discovered.inventory
            payload: dict[str, object] = {
                "git_available": discovered.has_git,
                "documents": [path.as_posix() for path in discovered.documents],
                "test_logs": [path.as_posix() for path in discovered.test_logs],
                "inventory_counts": {
                    "files": len(inventory.files),
                    "source_files": len(inventory.source_files),
                    "test_files": len(inventory.test_files),
                },
                "warnings": [warning.to_dict() for warning in discovered.warnings],
            }
            if discovered.git_authors:
                payload["git_authors"] = [
                    {"name": item.name, "email": item.email, "commits": item.commits}
                    for item in discovered.git_authors
                ]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.command == "export-evidence":
            root = args.project_dir.resolve()
            if args.list:
                entries = load_evidence_index(root)
                print(json.dumps([entry for entry in entries], ensure_ascii=False, indent=2))
                return 0
            exported: list[dict[str, object]] = []
            if args.git_commit is not None:
                entry = export_git_commit(root, args.git_commit)
                exported.append(entry.to_dict())
            if args.file is not None:
                entry = export_project_file(root, args.file)
                exported.append(entry.to_dict())
            if args.session_export is not None:
                entry = export_session_evidence(
                    root,
                    args.session_export,
                    source=args.source,
                    authorization=args.authorization,
                )
                exported.append(entry.to_dict())
            if not exported:
                raise ValueError(
                    "export-evidence needs one of --git-commit, --file, --session-export, or --list"
                )
            print(
                json.dumps(
                    {
                        "evidence_dir": (root / ".learntrace" / "evidence").as_posix(),
                        "exported": exported,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "authorize-full-read":
            record = record_full_read_authorization(
                args.project_dir,
                args.session_export,
                source=args.source,
                authorized_at=args.authorized_at,
            )
            print(
                json.dumps(
                    {
                        "recorded": record,
                        "note": (
                            "Full-read authorization recorded for this exact file. "
                            "You may now run export-evidence --session-export <path> "
                            "--source <host> --authorization full."
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "parse":
            output = args.output or args.project_dir.resolve() / ".learntrace/task2-result.json"
            events, warnings = _parse_project(args, output)
            print(f"Wrote parse result: {output} (events={events}, warnings={warnings})")
            return 0
        if args.command == "git-evidence":
            result = export_git_evidence(
                args.project_dir.resolve(),
                args.commit,
                paths=tuple(args.path),
                output_dir=args.output_dir,
                max_chars=args.max_chars,
            )
            print(
                f"Wrote Git evidence: {result.index_path} "
                f"(commit={result.commit_id}, artifacts={result.artifact_count})"
            )
            return 0
        if args.command == "git-index":
            result = write_git_history_index(
                args.project_dir.resolve(),
                output_path=args.output,
            )
            print(
                f"Wrote Git history index: {result.output_path} "
                f"(commits={result.total_commits}, complete={result.complete})"
            )
            return 0
        if args.command == "git-tree":
            result = write_git_tree(
                args.project_dir.resolve(),
                args.revision,
                output_path=args.output,
            )
            print(
                f"Wrote Git tree: {result.output_path} "
                f"(record={result.record_id}, files={result.item_count})"
            )
            return 0
        if args.command == "git-file":
            start_line, end_line = args.lines or (1, 200)
            start_byte, end_byte = args.bytes or (None, None)
            result = write_git_file(
                args.project_dir.resolve(),
                args.revision,
                args.path,
                start_line=start_line,
                end_line=end_line,
                start_byte=start_byte,
                end_byte=end_byte,
                output_path=args.output,
            )
            print(
                f"Wrote Git file evidence: {result.output_path} "
                f"(record={result.record_id}, available={bool(result.item_count)})"
            )
            return 0
        if args.command == "git-worktree":
            result = write_git_worktree(
                args.project_dir.resolve(),
                output_path=args.output,
                max_chars=args.max_chars,
            )
            print(
                f"Wrote Git worktree evidence: {result.output_path} "
                f"(record={result.record_id}, entries={result.item_count})"
            )
            return 0
        if args.command == "adapt":
            export_paths = tuple(args.export_path)
            if args.authorized and len(export_paths) != 1:
                raise ValueError(
                    "--authorized is only valid with one export; use --authorize-export per path"
                )
            authorized_paths = export_paths if args.authorized else tuple(args.authorize_export)
            result = _TRACE_EXPORT_ADAPTERS[args.source](
                export_paths,
                authorized_paths=authorized_paths,
                project_root=args.project_root,
            )
            output = (
                args.output or (args.project_root or Path.cwd()) / ".learntrace/task3-result.json"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            write_trace_result(result, output)
            print(
                f"Wrote trace result: {output} "
                f"(status={result.status}, events={len(result.events)}, "
                f"warnings={len(result.warnings)})"
            )
            return 0
        if args.command in ("verify-narrative", "render-narrative"):
            payload = load_payload(args.payload_path)
            archive = load_archive(args.archive_path)
            # --variant 先落到 payload 副本上,再对最终 payload 执行 verify:
            # 渲染哪一版就必须通过哪一版的红线,不能用另一版的验证结果
            # 绕过版本边界(如 working 版未经 submitted 必填项检查直接上交)。
            final_payload = payload
            if args.command == "render-narrative" and args.variant is not None:
                final_payload = copy.deepcopy(payload)
                final_payload["variant"] = args.variant
            violations = verify_payload(final_payload, archive)
            if violations:
                print(
                    json.dumps(
                        {"valid": False, "violations": violations},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 1
            if args.command == "verify-narrative":
                print(
                    json.dumps(
                        {"valid": True, "violations": []},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            markdown = render_narrative_markdown(final_payload, archive)
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(markdown, encoding="utf-8")
                print(f"Wrote narrative render: {args.output}")
            else:
                print(markdown, end="")
            return 0

        root = args.project_dir.resolve()
        work_dir = root / ".learntrace"
        parse_output = work_dir / "task2-result.json"
        output = args.output or root / "learning-record.md"
        archive_snapshot = work_dir / "archive-records.json"
        if args.confirmations:
            conflicting_options: list[str] = []
            if args.document is not None:
                conflicting_options.append("--document")
            if args.test_log is not None:
                conflicting_options.append("--test-log")
            if args.no_git:
                conflicting_options.append("--no-git")
            if args.max_commits is not None:
                conflicting_options.append("--max-commits")
            if args.find_copies_harder:
                conflicting_options.append("--find-copies-harder")
            if args.author is not None:
                conflicting_options.append("--author")
            if args.opencode_export:
                conflicting_options.append("--opencode-export")
            if args.authorized:
                conflicting_options.append("--authorized")
            if args.authorize_opencode_export:
                conflicting_options.append("--authorize-opencode-export")
            if args.claude_code_export:
                conflicting_options.append("--claude-code-export")
            if args.authorize_claude_code_export:
                conflicting_options.append("--authorize-claude-code-export")
            if args.codex_export:
                conflicting_options.append("--codex-export")
            if args.authorize_codex_export:
                conflicting_options.append("--authorize-codex-export")
            if conflicting_options:
                joined = ", ".join(conflicting_options)
                raise ValueError(
                    "--confirmations reuses the existing analysis snapshot and cannot be "
                    f"combined with evidence collection options: {joined}"
                )
            archive_result = write_learning_record_result(
                work_dir,
                output_path=output,
                records_output_path=archive_snapshot,
                questions_output_path=work_dir / "learning-questions.md",
                confirmation_paths=tuple(args.confirmations),
                snapshot_path=archive_snapshot,
                artifact_registry_root=root,
            )
            print(f"Applied confirmations to analysis snapshot: {archive_snapshot}")
            print(f"Wrote learning record: {archive_result.output_path}")
            return 0

        events, warnings = _parse_project(
            args,
            parse_output,
            excluded_documents=(
                output,
                archive_snapshot,
                work_dir / "learning-questions.md",
                work_dir / "task3-result.json",
            ),
        )
        export_paths = tuple(args.opencode_export)
        if args.authorized and len(export_paths) > 1:
            raise ValueError(
                "--authorized is only valid with one export; "
                "use --authorize-opencode-export per path"
            )
        if args.authorized and (args.claude_code_export or args.codex_export):
            raise ValueError(
                "--authorized only covers the OpenCode export; use "
                "--authorize-claude-code-export or --authorize-codex-export per path"
            )
        authorized_paths = (
            export_paths if args.authorized else tuple(args.authorize_opencode_export)
        )
        opencode_result = adapt_opencode_exports(
            export_paths,
            authorized_paths=authorized_paths,
            project_root=root,
        )
        trace_result = _merge_trace_results(
            (
                (
                    "opencode",
                    opencode_result,
                ),
                (
                    "claude-code",
                    adapt_claude_code_exports(
                        tuple(args.claude_code_export),
                        authorized_paths=tuple(args.authorize_claude_code_export),
                        project_root=root,
                    ),
                ),
                (
                    "codex",
                    adapt_codex_exports(
                        tuple(args.codex_export),
                        authorized_paths=tuple(args.authorize_codex_export),
                        project_root=root,
                    ),
                ),
            )
        )
        if (args.opencode_export or args.authorize_opencode_export or args.authorized) and (
            opencode_result.status != TraceInputStatus.PARSED
        ):
            print(
                f"warning: requested OpenCode export(s) produced 0 events "
                f"(status={opencode_result.status.value}); the trace archive has no "
                "traces to adapt. If this is unexpected, check that each export "
                "path exists and is authorized.",
                file=sys.stderr,
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        write_trace_result(trace_result, work_dir / "task3-result.json")
        archive_result = write_learning_record_result(
            work_dir,
            output_path=output,
            records_output_path=archive_snapshot,
            questions_output_path=work_dir / "learning-questions.md",
            confirmation_paths=tuple(args.confirmations),
            artifact_registry_root=root,
            trace_result=trace_result,
        )
        print(f"Wrote parse result: {parse_output} (events={events}, warnings={warnings})")
        print(
            "Wrote trace result: "
            f"{work_dir / 'task3-result.json'} "
            f"(status={trace_result.status}, events={len(trace_result.events)}, "
            f"warnings={len(trace_result.warnings)})"
        )
        print(f"Wrote learning record: {archive_result.output_path}")
        return 0
    except (FileNotFoundError, OSError, PermissionError, ValueError) as exc:
        parser.exit(1, f"{parser.prog}: error: {exc}\n")


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    # Delegate before the wrapper parser consumes ``--help`` or archive-only
    # options, so the unified entry point exposes the complete archive CLI.
    if values and values[0] == "archive":
        return archive_main(values[1:])
    # Preserve the original flat archive invocation for existing users.
    if values and values[0] not in _COMMANDS and values[0] not in {"-h", "--help", "--version"}:
        return archive_main(values)
    parser = build_parser()
    return _run_command(parser.parse_args(values), parser)


__all__ = ["build_parser", "main"]
