"""Run repository quality gates and verify a built wheel in a clean environment.

Install ``.[dev]`` before running this script with Python 3.11 or later. Commands
run without a shell, fail on the first nonzero exit status, and have a ten-minute
deadline. Advisory lookup requires network access unless explicitly skipped.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TIMEOUT_SECONDS = 600
_SMOKE_IMPORT = """\
import importlib.metadata
import pathlib
import sdk_atlas
import sysconfig

assert sdk_atlas.__version__ == importlib.metadata.version("sdk-atlas")
package_file = pathlib.Path(sdk_atlas.__file__).resolve()
site_packages = pathlib.Path(sysconfig.get_paths()["purelib"]).resolve()
assert package_file.is_relative_to(site_packages), package_file
assert (package_file.parent / "py.typed").is_file()
"""


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> int:
    """Return a child command's status while preserving its diagnostic output."""
    print(f"+ {' '.join(command)}", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        # This process boundary translates launch failures into an actionable
        # gate failure; the original exception remains visible to the operator.
        print(f"Quality gate could not complete: {error}", file=sys.stderr)
        return 1
    return result.returncode


def _verify_wheel(build_dir: Path, environment: dict[str, str]) -> int:
    """Install the built wheel and verify imports, entry points, and an offline scan."""
    wheels = list(build_dir.glob("*.whl"))
    if len(wheels) != 1:
        print("Expected exactly one wheel from the clean build.", file=sys.stderr)
        return 1

    target = build_dir / "wheel-environment"
    executable_dir = target / ("Scripts" if os.name == "nt" else "bin")
    python = executable_dir / ("python.exe" if os.name == "nt" else "python")
    console = executable_dir / ("sdk-atlas.exe" if os.name == "nt" else "sdk-atlas")
    commands = [
        [sys.executable, "-m", "venv", str(target)],
        [str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])],
        [str(python), "-I", "-c", _SMOKE_IMPORT],
        [str(python), "-I", "-m", "sdk_atlas", "--help"],
        [str(console), "--help"],
        [
            str(python),
            "-I",
            "-m",
            "sdk_atlas",
            "--package",
            "demo_sdk",
            "--source",
            str(_ROOT / "examples"),
            "--out",
            str(build_dir / "wheel-inventory"),
        ],
    ]
    for command in commands:
        status = _run(command, cwd=build_dir, environment=environment)
        if status:
            return status
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run quality gates and return zero only when every requested gate passes.

    Args:
        argv: Optional command-line arguments; the process arguments are used
            when omitted. ``--skip-security`` permits offline advisory skipping.
            ``--artifacts`` retains validated distributions in the given path.

    Returns:
        The first failed child status, or zero after successful validation.

    Side Effects:
        Runs development tools and creates temporary build/virtual-environment
        files. ``--artifacts`` explicitly writes distribution copies. Dependency
        auditing contacts the configured advisory services.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-security",
        action="store_true",
        help="Skip network-based advisories; the complete gate still requires them.",
    )
    parser.add_argument(
        "--artifacts", type=Path, help="Retain validated sdist and wheel artifacts."
    )
    arguments = parser.parse_args(argv)
    environment = dict(os.environ)
    environment.setdefault("SOURCE_DATE_EPOCH", "315532800")
    # The smoke environment must not import local source through caller settings.
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    commands = [
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "mypy"],
        [sys.executable, "-m", "pytest"],
    ]
    if not arguments.skip_security:
        commands.append([sys.executable, str(_ROOT / "scripts/audit_dependencies.py")])
    for command in commands:
        status = _run(command, cwd=_ROOT, environment=environment)
        if status:
            return status

    with tempfile.TemporaryDirectory(prefix="sdk-atlas-check-") as temporary:
        build_dir = Path(temporary)
        status = _run(
            [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--outdir",
                str(build_dir),
            ],
            cwd=_ROOT,
            environment=environment,
        )
        if status:
            return status
        status = _verify_wheel(build_dir, environment)
        if status:
            return status
        if arguments.artifacts is not None:
            # The destination is explicitly requested; retain only validated
            # distribution files, never the temporary verification environment.
            destination = Path(arguments.artifacts).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            for artifact in sorted(build_dir.iterdir()):
                if artifact.is_file() and artifact.name.endswith((".whl", ".tar.gz")):
                    shutil.copy2(artifact, destination / artifact.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
