"""Runtime paths and security-sensitive launch settings for the local UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_path, user_data_path


@dataclass(frozen=True)
class UIPaths:
    data_dir: Path
    cache_dir: Path
    database: Path
    pi_sessions: Path

    @property
    def model_config(self) -> Path:
        return self.data_dir / "model-config.json"

    @classmethod
    def defaults(cls) -> UIPaths:
        data = user_data_path("LearnTrace", "LearnTrace")
        cache = user_cache_path("LearnTrace", "LearnTrace")
        return cls(
            data,
            cache,
            data / "ui.sqlite3",
            data / "pi-sessions",
        )

    def ensure(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.pi_sessions):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class UISettings:
    project: Path
    host: str = "127.0.0.1"
    port: int = 0
    paths: UIPaths | None = None

    @classmethod
    def create(
        cls, project: Path, *, host: str = "127.0.0.1", port: int = 0, paths: UIPaths | None = None
    ) -> UISettings:
        resolved = project.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"project is not a directory: {resolved}")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("LearnTrace UI may only listen on a loopback address")
        selected = paths or UIPaths.defaults()
        selected.ensure()
        return cls(resolved, host, port, selected)
