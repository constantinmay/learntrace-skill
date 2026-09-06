"""Mirror the canonical Skill into the wheel package without editing its source."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills" / "learntrace"
TARGET = ROOT / "src" / "learntrace" / "ui" / "skill"


def matches() -> bool:
    if not TARGET.is_dir():
        return False
    source_files = {
        path.relative_to(SOURCE): path.read_bytes() for path in SOURCE.rglob("*") if path.is_file()
    }
    target_files = {
        path.relative_to(TARGET): path.read_bytes() for path in TARGET.rglob("*") if path.is_file()
    }
    return source_files == target_files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        if matches():
            return 0
        parser.exit(1, "packaged UI Skill differs from skills/learntrace\n")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
