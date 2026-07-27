"""Small, deterministic privacy helpers for trace summaries."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_REDACTED = "[REDACTED]"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SAFE_EXECUTABLE_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.DOTALL)

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    flags=re.IGNORECASE | re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)\bAuthorization\s*:\s*Bearer\s+(?!\[REDACTED\])[^,\s;]+")
_CREDENTIAL_URL_RE = re.compile(r"(?i)\b(https?://)(?!\[REDACTED\]@)[^/\s:@]+:[^@/\s]+@")
_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    \b(password|passwd|pwd|token|api[_-]?key|apikey|secret)
    \s*[:=]\s*
    (?!\[REDACTED\])
    (?:"[^"]*"|'[^']*'|[^\s,;&]+)
    """
)
_TOKEN_PREFIX_RE = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})\b"
)
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")


def redact_sensitive_text(value: str, *, limit: int = 160) -> str:
    """Redact common secret shapes before applying a deterministic length cap."""

    if limit < 0:
        raise ValueError("limit must be non-negative")

    redacted = _PRIVATE_KEY_RE.sub(_REDACTED, value)
    redacted = _BEARER_RE.sub(f"Authorization: Bearer {_REDACTED}", redacted)
    redacted = _CREDENTIAL_URL_RE.sub(rf"\1{_REDACTED}@", redacted)
    redacted = _ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={_REDACTED}", redacted)
    redacted = _TOKEN_PREFIX_RE.sub(_REDACTED, redacted)
    redacted = _AWS_ACCESS_KEY_RE.sub(_REDACTED, redacted)
    return redacted[:limit]


def _path_style(value: str) -> str | None:
    if _WINDOWS_ABSOLUTE_RE.match(value) or value.startswith(("\\", "//")):
        return "windows"
    if value.startswith("/"):
        return "posix"
    return None


def _normalized_parts(value: str) -> tuple[str, ...] | None:
    normalized = value.replace("\\", "/")
    parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
    if not parts or ".." in parts:
        return None
    return parts


def normalize_project_path(value: str, project_root: Path | None) -> str:
    """Return a safe relative POSIX path or a non-identifying placeholder."""

    if (
        not value
        or _CONTROL_RE.search(value)
        or (_WINDOWS_DRIVE_RE.match(value) and not _WINDOWS_ABSOLUTE_RE.match(value))
    ):
        return "[unsafe-path]"

    value_style = _path_style(value)
    value_parts = _normalized_parts(value)
    if value_parts is None:
        return "[unsafe-path]"

    if value_style is None:
        return "/".join(value_parts)
    if project_root is None:
        return "[absolute-path]"

    root_text = str(project_root)
    if value_style == "posix" and root_text.startswith("\\") and not root_text.startswith("\\\\"):
        # ``Path("/x")`` is rendered as ``\x`` on Windows; recover its POSIX intent.
        root_text = f"/{root_text.lstrip(chr(92))}"
    root_style = _path_style(root_text)
    root_parts = _normalized_parts(root_text)
    if root_style != value_style or root_parts is None:
        return "[outside-project]"

    if value_style == "windows":
        comparable_value = tuple(part.casefold() for part in value_parts)
        comparable_root = tuple(part.casefold() for part in root_parts)
    else:
        comparable_value = value_parts
        comparable_root = root_parts

    if (
        len(comparable_value) <= len(comparable_root)
        or comparable_value[: len(comparable_root)] != comparable_root
    ):
        return "[outside-project]"

    return "/".join(value_parts[len(root_parts) :])


def _command_tokens(value: str) -> list[str]:
    try:
        return shlex.split(value, posix=True)
    except ValueError:
        return value.split()


def summarize_command(value: str) -> str:
    """Keep only a conservative executable/subcommand label, never arguments."""

    tokens = _command_tokens(value)
    while tokens and _ENV_ASSIGNMENT_RE.fullmatch(tokens[0]):
        tokens.pop(0)
    if not tokens:
        return "unknown-command"

    executable = tokens[0].replace("\\", "/").rsplit("/", maxsplit=1)[-1].casefold()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if not _SAFE_EXECUTABLE_RE.fullmatch(executable):
        return "unknown-command"

    if executable == "git" and len(tokens) > 1:
        subcommand = tokens[1].casefold()
        if _SAFE_EXECUTABLE_RE.fullmatch(subcommand) and not subcommand.startswith("-"):
            return f"git {subcommand}"
    if executable == "uv" and len(tokens) > 2 and tokens[1:3] == ["run", "pytest"]:
        return "uv run pytest"
    if executable in {"npm", "pnpm", "yarn"} and len(tokens) > 1:
        subcommand = tokens[1].casefold()
        if subcommand in {"test", "run"}:
            return f"{executable} {subcommand}"
    return executable
