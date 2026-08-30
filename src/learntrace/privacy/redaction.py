"""Small, deterministic privacy helpers for trace summaries."""

from __future__ import annotations

import re
import shlex
from enum import StrEnum
from pathlib import Path

_REDACTED = "[REDACTED]"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SAFE_EXECUTABLE_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,64}$")
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.DOTALL)
_HOME_PREFIX_RE = re.compile(
    r"^(?:~[^\\/]*|\$(?:HOME|USERPROFILE|HOMEPATH)|"
    r"\$\{(?:HOME|USERPROFILE|HOMEPATH)\}|\$env:(?:HOME|USERPROFILE|HOMEPATH)|"
    r"%(?:HOME|USERPROFILE|HOMEPATH)%)(?:[\\/]|$)",
    re.IGNORECASE,
)

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

    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "[unsafe-path]"

    if (
        not value
        or _CONTROL_RE.search(value)
        or _HOME_PREFIX_RE.match(value)
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
    """Keep a conservative executable/subcommand label, never arguments.

    A shell prefix such as ``cd frontend &&`` is navigation rather than the
    operation we want to describe, so a later command in the chain may be
    selected.  Only a small allow-list of safe subcommands is retained; all
    other arguments (including paths and option values) are discarded.
    """

    # Choose the first non-navigation command in a simple shell chain.  This
    # lets the segment classifier recognize ``cd frontend && npm run build``
    # without persisting ``frontend`` or any later arguments.  Quoted shell
    # operators are uncommon in adapter input and remain part of their token.
    command_parts = re.split(r"(?:&&|\|\||[;|])", value)
    tokens: list[str] = []
    for part in command_parts:
        candidate = _command_tokens(part)
        while candidate and _ENV_ASSIGNMENT_RE.fullmatch(candidate[0]):
            candidate.pop(0)
        if not candidate:
            continue
        executable_candidate = candidate[0].replace("\\", "/").rsplit("/", maxsplit=1)[-1]
        if executable_candidate.casefold() in {"cd", "pushd", "popd"} and len(command_parts) > 1:
            continue
        tokens = candidate
        break
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
    if (
        executable == "uv"
        and len(tokens) > 2
        and [token.casefold() for token in tokens[1:3]]
        == [
            "run",
            "pytest",
        ]
    ):
        return "uv run pytest"
    if executable == "uv" and len(tokens) > 1:
        if tokens[1].casefold() == "sync":
            return "uv sync"
        if len(tokens) > 2 and tokens[1].casefold() == "pip" and tokens[2].casefold() == "install":
            return "uv pip install"
    if executable in {"pip", "pip3", "pipx"} and len(tokens) > 1:
        subcommand = tokens[1].casefold()
        if subcommand in {"install", "sync"}:
            return f"{executable} {subcommand}"
    if executable in {"npm", "pnpm", "yarn"} and len(tokens) > 1:
        subcommand = tokens[1].casefold()
        if subcommand in {"install", "i", "add", "ci"}:
            return f"{executable} install"
        if subcommand == "run" and len(tokens) > 2:
            script = tokens[2].casefold()
            if script in {"test", "build", "bundle", "deploy", "release"}:
                return f"{executable} run {script}"
        if subcommand in {"test", "run"}:
            return f"{executable} {subcommand}"
    if (
        executable in {"make", "cargo", "go", "mvn", "gradle", "docker", "podman"}
        and len(tokens) > 1
    ):
        subcommand = tokens[1].casefold()
        if subcommand in {
            "test",
            "build",
            "package",
            "assemble",
            "install",
            "compose",
            "push",
            "get",
            "add",
        }:
            return f"{executable} {subcommand}"
    return executable


class CommandCategory(StrEnum):
    """Small, human-readable command groups used by work-segment summaries."""

    INSTALL = "装依赖"
    TEST = "跑测试"
    BUILD_DEPLOY = "构建部署"
    INSPECT = "查文件"
    OTHER = "其他"


def classify_command(value: str) -> CommandCategory:
    """Classify a command without retaining its arguments or paths.

    Classification is intentionally conservative and deterministic.  It is
    applied to the already minimized command label when possible (for example
    ``uv run pytest`` or ``git status``), but also accepts a raw command for
    callers that classify before minimization.  When several shell commands
    are chained, the first matching category by the documented priority is
    returned; no command text is returned by this helper.
    """

    if not value.strip():
        return CommandCategory.OTHER
    lowered = value.casefold().strip()
    direct_labels = {
        CommandCategory.INSTALL.value.casefold(): CommandCategory.INSTALL,
        CommandCategory.TEST.value.casefold(): CommandCategory.TEST,
        CommandCategory.BUILD_DEPLOY.value.casefold(): CommandCategory.BUILD_DEPLOY,
        CommandCategory.INSPECT.value.casefold(): CommandCategory.INSPECT,
        CommandCategory.OTHER.value.casefold(): CommandCategory.OTHER,
    }
    if lowered in direct_labels:
        return direct_labels[lowered]
    tokens = _command_tokens(lowered)
    while tokens and _ENV_ASSIGNMENT_RE.fullmatch(tokens[0]):
        tokens.pop(0)
    compact = " ".join(tokens)

    # Dependency installation/update commands.
    if (
        re.search(r"\b(?:pip|pip3|pipx)\s+(?:install|sync)", compact)
        or re.search(r"\buv\s+(?:pip\s+)?(?:install|sync)", compact)
        or re.search(r"\b(?:npm|pnpm|yarn)\s+(?:install|i|add|ci)\b", compact)
        or re.search(r"\b(?:poetry|conda|mamba|apt|apt-get|apk|brew|gem)\s+.*\binstall\b", compact)
        or re.search(r"\b(?:cargo)\s+add\b", compact)
        or re.search(r"\bgo\s+get\b", compact)
    ):
        return CommandCategory.INSTALL

    # Test commands.  Match both direct runners and common package-manager
    # wrappers (``npm run test`` / ``python -m pytest``).
    if (
        re.search(r"\bpytest\b", compact)
        or re.search(r"\b(?:unittest|nose|tox|vitest|jest)\b", compact)
        or re.search(r"\b(?:go|cargo|mvn|gradle)\s+test\b", compact)
        or re.search(r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b", compact)
        or re.search(r"\bmake\s+test\b", compact)
    ):
        return CommandCategory.TEST

    # Build/deploy commands.  ``npm run`` without a script remains OTHER;
    # only an explicit build/deploy verb is classified here.
    if (
        re.search(r"\b(?:npm|pnpm|yarn)\s+run\s+(?:build|bundle|deploy|release)\b", compact)
        or re.search(r"\b(?:vite|webpack|rollup|tsc)\s+(?:build|compile)\b", compact)
        or re.search(r"\b(?:cargo|go)\s+build\b", compact)
        or re.search(r"\b(?:mvn|gradle)\s+(?:package|install|assemble)\b", compact)
        or re.search(r"\b(?:docker|podman)\s+(?:build|compose|push|run)\b", compact)
        or re.search(r"\b(?:kubectl|helm|terraform)\s+(?:apply|upgrade|deploy|install)\b", compact)
        or re.search(r"\b(?:make|vercel|netlify)\s+(?:build|deploy)\b", compact)
        or re.search(r"\b(?:build|bundle|deploy|release)\b", compact)
    ):
        return CommandCategory.BUILD_DEPLOY

    # Read-only inspection commands.  Git mutating commands are deliberately
    # not called "查文件"; they fall through to OTHER.
    if (
        re.search(
            r"(?:^|\s)(?:ls|dir|find|rg|grep|cat|type|pwd|tree|head|tail|sed|less|more|file)(?:\s|$)",
            compact,
        )
        or re.search(r"\bgit\s+(?:status|log|diff|show)\b", compact)
        or re.search(r"\bget-content\b", compact)
    ):
        return CommandCategory.INSPECT

    return CommandCategory.OTHER
