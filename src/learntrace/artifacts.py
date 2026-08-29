"""Shared boundaries for LearnTrace-generated and Git-internal paths.

The evidence collectors must agree on which files describe the inspected
project and which files were produced by LearnTrace itself.  This module is
deliberately independent from the parser modules so discovery, Git navigation,
the CLI, and archive writing can all use the same decision.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

_REGISTRY_PATH = PurePosixPath(".learntrace/generated-artifacts.json")
_GENERATED_NAMES = frozenset({"learning-record.md", "learning-questions.md"})


def _builtin_reason(path: str) -> str | None:
    parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    if ".git" in parts:
        return "git_internal_path"
    if ".learntrace" in parts:
        return "learntrace_generated_artifact"
    if parts and parts[-1] in _GENERATED_NAMES:
        return "learntrace_generated_artifact"
    return None


def _normalise_repository_path(value: str | Path) -> str:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 for character in raw)
        or (path.parts and path.parts[0].endswith(":"))
    ):
        raise ValueError(f"unsafe repository path: {value!r}")
    return path.as_posix()


def _project_relative(root: Path, value: str | Path) -> str | None:
    candidate = Path(value)
    candidate = candidate if candidate.is_absolute() else root / candidate
    try:
        return candidate.resolve(strict=False).relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise


def _registered_paths(root: Path) -> frozenset[str]:
    registry = root / Path(_REGISTRY_PATH.as_posix())
    try:
        raw_payload: object = json.loads(registry.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return frozenset()
    payload = cast(dict[str, object], raw_payload) if isinstance(raw_payload, dict) else {}
    values = payload.get("paths")
    if not isinstance(values, list):
        return frozenset()
    normalised: set[str] = set()
    for value in cast(list[object], values):
        if not isinstance(value, str):
            continue
        try:
            normalised.add(_normalise_repository_path(value))
        except ValueError:
            continue
    return frozenset(normalised)


@dataclass(frozen=True, slots=True)
class EvidencePathPolicy:
    """Classify repository-relative paths consistently across collectors."""

    root: Path
    generated_paths: frozenset[str]

    @classmethod
    def load(
        cls,
        project_root: Path,
        *,
        additional_generated: Iterable[str | Path] = (),
    ) -> EvidencePathPolicy:
        root = project_root.resolve(strict=True)
        generated = set(_registered_paths(root))
        for value in additional_generated:
            relative = _project_relative(root, value)
            if relative is not None:
                generated.add(relative)
        return cls(root=root, generated_paths=frozenset(generated))

    def reason(self, value: str | Path) -> str | None:
        try:
            path = _normalise_repository_path(value)
        except ValueError:
            return "unsafe_repository_path"
        builtin = _builtin_reason(path)
        if builtin is not None:
            return builtin
        path_key = path.casefold() if os.name == "nt" else path
        registered = {item.casefold() if os.name == "nt" else item for item in self.generated_paths}
        if path_key in registered:
            return "learntrace_generated_artifact"
        return None

    def allows(self, value: str | Path) -> bool:
        return self.reason(value) is None


def register_generated_artifacts(project_root: Path, paths: Iterable[str | Path]) -> None:
    """Persist successful custom outputs so later discovery excludes them."""
    root = project_root.resolve(strict=True)
    registered = set(_registered_paths(root))
    for value in paths:
        relative = _project_relative(root, value)
        # Built-in outputs are already excluded without registry state.  The
        # registry exists only for custom output names chosen by callers.
        if relative is not None and _builtin_reason(relative) is None:
            registered.add(relative)
    payload = {
        "schema_version": "v0",
        "paths": sorted(registered),
    }
    registry = root / Path(_REGISTRY_PATH.as_posix())
    _atomic_write(registry, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
