"""Command-line entry point for LearnTrace."""

import argparse
from collections.abc import Sequence

from learntrace import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learntrace",
        description="Build a traceable learning portfolio from local project evidence.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
