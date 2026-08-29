"""Guard the reviewer-raised packaging regression: the built wheel must be a
usable package with schemas bundled.

Regression for two distinct past failures:
- schema files missing from the wheel (``could not locate schemas/v0``);
- source laid out as ``src/learntrace/...`` with no top-level package
  (``ModuleNotFoundError: No module named 'learntrace'`` after install).

The test builds the wheel with ``pip wheel --no-deps`` (the declared
``uv_build`` backend), asserts the archive layout, then installs it into an
isolated target dir and exercises ``import`` + ``ContractValidator()`` against
that installation (not the repo ``src``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable

# Top-level package + schema files that must survive packaging.
_REQUIRED_PACKAGE_FILES = (
    "learntrace/__init__.py",
    "learntrace/cli.py",
)
_REQUIRED_SCHEMA_FILES = (
    "learntrace/schemas/v0/observable-event.schema.json",
    "learntrace/schemas/v0/learning-node-candidate.schema.json",
    "learntrace/schemas/v0/student-confirmation.schema.json",
)


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": _PATH,
            "PYTHONPATH": "",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
    )
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


_PATH = str(Path(PYTHON).parent)


SITE_PACKAGES = next(p for p in sys.path if p and (Path(p) / "jsonschema").is_dir())

# Runtime dependencies of learntrace that the wheel test installs with
# --no-deps: seeding them from the active site-packages keeps the test
# hermetic (no network fetch) while still isolating the learntrace package.
_RT_DEPS = (
    "attr",
    "attrs",
    "jsonschema",
    "jsonschema_specifications",
    "referencing",
    "rpds",
    "rpds_py",
    "rfc3339_validator",
    "typing_extensions",
)


def _seed_runtime_deps(target: Path) -> bool:
    """Copy learntrace's runtime deps from the active venv into ``target``.

    Returns ``False`` (and the wheels gets built elsewhere) if they cannot be
    located; callers treat that as 'skip install-path assertions'.
    """
    src = Path(SITE_PACKAGES)
    for name in _RT_DEPS:
        for match in src.glob(f"{name}*"):
            if match.is_dir():
                dst = target / match.name
                if not dst.exists():
                    shutil.copytree(match, dst)
            elif match.is_file():
                dst = target / match.name
                if not dst.exists():
                    shutil.copy2(match, dst)
    return (target / "jsonschema").is_dir()


def _build_wheel(tmp_path: Path) -> Path:
    wheels_dir = tmp_path / "wheels"
    wheels_dir.mkdir()
    base = [PYTHON, "-m", "pip", "wheel", str(REPO_ROOT), "--no-deps", "-w", str(wheels_dir)]
    result = _run(base, cwd=tmp_path)
    if result.returncode != 0:
        # The uv_build backend is already present in the venv; avoid a
        # network fetch of a fresh isolated build environment.
        no_isolated = _run([*base, "--no-build-isolation"], cwd=tmp_path)
        if no_isolated.returncode != 0 or not list(wheels_dir.glob("*.whl")):
            pytest.skip(f"wheel build not available in this environment:\n{no_isolated.stderr}")
        result = no_isolated
    wheels = list(wheels_dir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {[w.name for w in wheels]}"
    return wheels[0]


def test_wheel_contains_top_level_package_and_schemas(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    for required in (*_REQUIRED_PACKAGE_FILES, *_REQUIRED_SCHEMA_FILES):
        assert required in names, f"wheel is missing {required}"


def test_clean_install_imports_and_validates_from_wheel(tmp_path: Path) -> None:
    """Install the built wheel into an isolated dir and exercise it there,
    proving the package (not the repo src) is importable and schemas load."""
    wheel = _build_wheel(tmp_path)
    target = tmp_path / "site"
    result = _run(
        [PYTHON, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    # Seed learntrace's runtime deps (excluded via --no-deps) from the active
    # site-packages so the wheel package is exercised without a network fetch.
    if not _seed_runtime_deps(target):
        pytest.skip("could not locate jsonschema deps for offline wheel test")

    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import learntrace;"
        "from learntrace.models import ContractValidator;"
        "cv = ContractValidator();"
        "from learntrace import cli;"
        "print('OK', v := learntrace.__file__)"
    )
    result = _run([sys.executable, "-c", probe, str(target)], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert str(target) in result.stdout, f"imported from repo src, not wheel: {result.stdout}"


def test_cli_entry_point_runs_from_installed_wheel(tmp_path: Path) -> None:
    """The installed CLI binary must start and print usage without a repo src
    on the path."""
    wheel = _build_wheel(tmp_path)
    venv_dir = tmp_path / "venv"
    result = _run([PYTHON, "-m", "venv", str(venv_dir)], cwd=tmp_path)
    if result.returncode != 0:
        pytest.skip(f"venv creation not available:\n{result.stderr}")
    venv_python = (
        venv_dir / "Scripts" / "python.exe"
        if sys.platform == "win32"
        else venv_dir / "bin" / "python"
    )
    site_dir = (
        venv_dir / "Lib" / "site-packages"
        if sys.platform == "win32"
        else venv_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_dir.mkdir(parents=True, exist_ok=True)
    if not _seed_runtime_deps(site_dir):
        pytest.skip("could not locate jsonschema deps for offline wheel test")
    result = _run(
        [str(venv_python), "-m", "pip", "install", "-q", "--no-deps", str(wheel)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    result = _run(
        [str(venv_python), "-c", "import learntrace; print(learntrace.__file__)"], cwd=tmp_path
    )
    assert result.returncode == 0, result.stderr
    assert "venv" in result.stdout and "site-packages" in result.stdout, result.stdout

    cli_bin = (
        venv_dir / "Scripts" / "learntrace.exe"
        if sys.platform == "win32"
        else venv_dir / "bin" / "learntrace"
    )
    result = _run([str(cli_bin), "--help"], cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Parse local evidence" in result.stdout, result.stdout
    assert "git-file" in result.stdout, result.stdout
    assert "run" in result.stdout, result.stdout
