"""Unified command-line entry point for the LearnTrace local pipeline."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from learntrace import __version__
from learntrace.adapters import adapt_opencode_export, write_trace_result
from learntrace.archive import main as archive_main
from learntrace.archive import write_learning_record_result
from learntrace.parsers import discover_static_materials, parse_static_materials, write_parse_result

_COMMANDS = frozenset({"adapt", "archive", "parse", "run"})


def _add_parse_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_dir", type=Path, help="Project repository to inspect.")
    parser.add_argument("--document", action="append", type=Path, default=None)
    parser.add_argument("--test-log", action="append", type=Path, default=None)
    parser.add_argument("--no-git", action="store_true", help="Do not read local Git history.")
    parser.add_argument("--max-commits", type=int, default=50)
    parser.add_argument("--find-copies-harder", action="store_true")


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

    adapt_parser = subparsers.add_parser("adapt", help="Adapt an OpenCode JSON export.")
    adapt_parser.add_argument("export_path", type=Path)
    adapt_parser.add_argument("--project-root", type=Path)
    adapt_parser.add_argument("--authorized", action="store_true")
    adapt_parser.add_argument("-o", "--output", type=Path)

    archive_parser = subparsers.add_parser("archive", help="Build Task 4 archive outputs.")
    archive_parser.add_argument("args", nargs=argparse.REMAINDER)

    run_parser = subparsers.add_parser("run", help="Run the local parse-to-archive pipeline.")
    _add_parse_options(run_parser)
    run_parser.add_argument("--opencode-export", type=Path)
    run_parser.add_argument("--authorized", action="store_true")
    run_parser.add_argument(
        "--confirmations",
        action="append",
        type=Path,
        default=[],
        help="Explicit student-confirmation JSON file (repeatable).",
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
    discovered = discover_static_materials(root, excluded_paths=excluded_documents)
    documents = discovered.documents if args.document is None else tuple(args.document)
    excluded = {path.resolve() for path in excluded_documents}
    documents = tuple(path for path in documents if (root / path).resolve() not in excluded)
    test_logs = discovered.test_logs if args.test_log is None else tuple(args.test_log)
    result = parse_static_materials(
        root,
        document_paths=documents,
        test_log_paths=test_logs,
        include_git=not args.no_git,
        max_commits=args.max_commits,
        find_copies_harder=args.find_copies_harder,
        inventory_excluded_paths=excluded_documents,
    )
    write_parse_result(result, output_path)
    return len(result.events), len(result.warnings)


def _run_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        if args.command == "archive":
            return archive_main(args.args)
        if args.command == "parse":
            output = args.output or args.project_dir.resolve() / ".learntrace/task2-result.json"
            events, warnings = _parse_project(args, output)
            print(f"Wrote parse result: {output} (events={events}, warnings={warnings})")
            return 0
        if args.command == "adapt":
            result = adapt_opencode_export(
                args.export_path,
                authorized=args.authorized,
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

        root = args.project_dir.resolve()
        work_dir = root / ".learntrace"
        parse_output = work_dir / "task2-result.json"
        output = args.output or root / "learning-record.md"
        events, warnings = _parse_project(args, parse_output, excluded_documents=(output,))
        trace_result = adapt_opencode_export(
            args.opencode_export,
            authorized=args.authorized,
            project_root=root,
        )
        work_dir.mkdir(parents=True, exist_ok=True)
        write_trace_result(trace_result, work_dir / "task3-result.json")
        archive_result = write_learning_record_result(
            work_dir,
            output_path=output,
            records_output_path=work_dir / "archive-records.json",
            questions_output_path=work_dir / "learning-questions.md",
            confirmation_paths=tuple(args.confirmations),
        )
        print(f"Wrote parse result: {parse_output} (events={events}, warnings={warnings})")
        print(f"Wrote learning record: {archive_result.output_path}")
        return 0
    except (FileNotFoundError, OSError, ValueError) as exc:
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
