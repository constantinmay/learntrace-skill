"""Compatibility wrapper for the archive CLI."""

from collections.abc import Sequence

from learntrace.archive import build_parser, main

__all__ = ["build_parser", "main", "Sequence"]
