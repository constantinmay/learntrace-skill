"""Task2 批量结果的本地 JSON 写出。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from _thread import LockType
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from learntrace.models import ContractValidator
from learntrace.parsers.types import ParseResult

_WINDOWS_REPLACE_ERRORS = frozenset({5, 32})
_REPLACE_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16)


@dataclass(slots=True)
class _PathLockEntry:
    lock: LockType = field(default_factory=threading.Lock)
    users: int = 0


_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, _PathLockEntry] = {}


def _path_lock_key(path: Path) -> str:
    """Return a process-local key that collapses equivalent output paths."""
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


@contextmanager
def _serialize_path(path: Path) -> Generator[None, None, None]:
    """Serialize writers targeting the same normalized path without leaking locks."""
    key = _path_lock_key(path)
    with _PATH_LOCKS_GUARD:
        entry = _PATH_LOCKS.setdefault(key, _PathLockEntry())
        entry.users += 1

    acquired = False
    try:
        entry.lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            entry.lock.release()
        with _PATH_LOCKS_GUARD:
            entry.users -= 1
            if entry.users == 0 and _PATH_LOCKS.get(key) is entry:
                del _PATH_LOCKS[key]


def _replace_with_retry(source: Path, target: Path) -> None:
    """Replace a file, briefly retrying Windows sharing/access violations."""
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, target)
            return
        except PermissionError as error:
            winerror = getattr(error, "winerror", None)
            if winerror not in _WINDOWS_REPLACE_ERRORS or attempt == len(_REPLACE_RETRY_DELAYS):
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


def write_parse_result(
    result: ParseResult,
    output_path: Path,
    *,
    validator: ContractValidator | None = None,
) -> None:
    """以 UTF-8 JSON 写出已校验结果；调用方负责选择本地输出路径。"""
    data = result.to_dict(validator)
    with _serialize_path(output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            _replace_with_retry(temporary, output_path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
