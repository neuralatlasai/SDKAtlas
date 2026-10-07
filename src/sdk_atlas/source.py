"""Locate Python packages, modules, stubs, and extensions without target imports."""

from __future__ import annotations

import importlib.machinery
import importlib.metadata
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PackageSource:
    """Own a resolved module file or package directory and metadata version."""

    root: Path
    version: str = "unknown"


def _validate_package(package: str) -> tuple[str, ...]:
    parts = tuple(package.split("."))
    if not parts or any(not part.isidentifier() for part in parts):
        raise ValueError("package must be an absolute dotted Python import name")
    return parts


def _version(directory: Path, distribution: str) -> str:
    normalized = re.sub(r"[-_.]+", "-", distribution).lower()
    for candidate in importlib.metadata.distributions(path=[str(directory)]):
        name = re.sub(r"[-_.]+", "-", candidate.metadata["Name"] or "").lower()
        if name == normalized:
            return candidate.version
    return "unknown"


def _native_module(path: Path) -> Path | None:
    """Match an exact extension module stem, including foreign platform tags."""
    # Current-interpreter suffixes take precedence when several ABIs coexist.
    for suffix in dict.fromkeys(
        (*importlib.machinery.EXTENSION_SUFFIXES, ".pyd", ".so")
    ):
        candidate = path.with_name(path.name + suffix)
        if candidate.is_file():
            return candidate
    tagged = (
        candidate
        for candidate in path.parent.glob(f"{path.name}.*")
        if candidate.suffix in {".pyd", ".so"}
        and re.fullmatch(
            re.escape(path.name) + r"(?:\.[A-Za-z0-9_-]+)+\.(?:pyd|so)",
            candidate.name,
        )
        and candidate.is_file()
    )
    # The number of matching ABIs is normally one. min avoids allocating a
    # sorted directory listing; comparison costs are bounded by filename size.
    return min(tagged, key=lambda candidate: candidate.name, default=None)


def _module_path(path: Path) -> Path | None:
    """Probe one import path, preferring source and adjacent static stubs."""
    if path.is_dir():
        initializer = path / "__init__"
        if (
            initializer.with_suffix(".py").is_file()
            or initializer.with_suffix(".pyi").is_file()
            or _native_module(initializer) is not None
        ):
            return path
    for suffix in (".py", ".pyi"):
        candidate = path.with_name(path.name + suffix)
        if candidate.is_file():
            return candidate
    native = _native_module(path)
    if native is not None:
        return native
    # A namespace package has no initializer. A concrete module with the same
    # name takes precedence, matching ordinary Python filesystem imports.
    return path if path.is_dir() else None


def _checkout_root(source: Path, parts: tuple[str, ...]) -> Path:
    base = source.expanduser().resolve(strict=True)
    if base.is_file():
        if base.suffix in {".py", ".pyi", ".pyd", ".so"}:
            return base
        raise ValueError(
            f"unsupported module file: {base}; use .py, .pyi, .pyd, or .so"
        )
    if not base.is_dir():
        raise ValueError(f"source is not a directory: {base}")
    for candidate in (base.joinpath(*parts), base.joinpath("src", *parts)):
        located = _module_path(candidate)
        if located is not None:
            return located.resolve()
    if (
        (base / "__init__.py").is_file()
        or (base / "__init__.pyi").is_file()
        or _native_module(base / "__init__") is not None
        or base.name == parts[-1]
    ):
        return base
    raise ValueError(
        f"cannot find {'/'.join(parts)} or src/{'/'.join(parts)} beneath {base}"
    )


@contextmanager
def package_source(
    package: str,
    *,
    source: Path | None = None,
    latest: bool = False,
    distribution: str = "",
    install_timeout: int = 120,
) -> Iterator[PackageSource]:
    """Yield source from a checkout, filesystem installation, or isolated wheel.

    Args:
        package: Absolute dotted Python import name; no import is performed.
        source: Optional repository, package directory, Python file, static stub,
            or native .pyd/.so extension. Adjacent source wins over stub files.
        latest: Download the latest wheel into a temporary directory.
        distribution: PyPI distribution name; defaults to the top-level package.
        install_timeout: Positive pip subprocess timeout in seconds.

    Yields:
        Package source valid for the lifetime of the context manager.

    Raises:
        ValueError: Invalid input, conflicting modes, or unavailable source.
        OSError: Filesystem or subprocess startup fails.
        RuntimeError: The isolated wheel installation fails or times out.

    Only latest mode uses the network. It installs wheels without dependencies,
    never executes target imports, and cleans up its temporary target on exit.
    Zip installations, custom import hooks, and built-in/frozen modules without
    filesystem evidence require --source pointing to source or static stubs.
    """
    parts = _validate_package(package)
    explicit_distribution = bool(distribution)
    distribution = distribution or parts[0]
    if (latest or explicit_distribution) and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*", distribution
    ):
        raise ValueError(
            "distribution must be a plain package name, without options or URLs"
        )
    if install_timeout <= 0:
        raise ValueError("install_timeout must be positive")
    if latest and source is not None:
        raise ValueError("--source and --latest are mutually exclusive")
    if source is not None:
        checkout = _checkout_root(source, parts)
        yield PackageSource(checkout, _version(checkout.parent, distribution))
        return
    if latest:
        with tempfile.TemporaryDirectory(prefix="sdk-atlas-") as temporary:
            directory = Path(temporary)
            target = directory / "target"
            with (directory / "pip.log").open("w+b") as log:
                try:
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "--disable-pip-version-check",
                            "--no-input",
                            "--no-deps",
                            "--only-binary=:all:",
                            "--target",
                            str(target),
                            distribution,
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=install_timeout,
                        check=False,
                    )
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError(
                        f"isolated wheel install exceeded {install_timeout} seconds"
                    ) from error
                if result.returncode:
                    log.seek(0)
                    detail = log.read(8192).decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"isolated wheel install failed ({result.returncode}): {detail}"
                    )
            root = _module_path(target.joinpath(*parts))
            if root is None:
                raise ValueError(
                    f"distribution {distribution!r} does not contain "
                    f"package {package!r}"
                )
            yield PackageSource(root, _version(target, distribution))
        return
    # PathFinder/find_spec can execute a dotted package's parents; direct path
    # probing deliberately accepts only ordinary filesystem installations.
    for entry in sys.path:
        directory = Path(entry or ".")
        root = _module_path(directory.joinpath(*parts))
        if root is not None:
            yield PackageSource(root.resolve(), _version(directory, distribution))
            return
    raise ValueError(
        f"package {package!r} has no filesystem source; use --source or --latest. "
        "Built-in/frozen modules require --source with static stubs."
    )
