"""Verify filesystem source discovery and isolated-wheel lifecycle behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from sdk_atlas import source as source_module
from sdk_atlas.source import package_source


@pytest.mark.parametrize("layout", ("direct", "repo", "src"))
def test_checkout_package_layouts_resolve_without_importing(
    tmp_path: Path, layout: str
) -> None:
    package = tmp_path / ("src" if layout == "src" else "") / "future"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "raise RuntimeError('source must not execute')\n", encoding="utf-8"
    )
    supplied = package if layout == "direct" else tmp_path

    with package_source("future", source=supplied) as located:
        assert located.root == package


@pytest.mark.parametrize("suffix", (".py", ".pyi", ".pyd", ".so"))
def test_explicit_module_files_are_supported(tmp_path: Path, suffix: str) -> None:
    module = tmp_path / f"future{suffix}"
    module.write_bytes(b"source or opaque extension")

    with package_source("future", source=module) as located:
        assert located.root == module


@pytest.mark.parametrize("suffix", (".py", ".pyi", ".abi3.so", ".cp313-win_amd64.pyd"))
def test_checkout_single_module_variants_are_discovered(
    tmp_path: Path, suffix: str
) -> None:
    (tmp_path / "src").mkdir()
    module = tmp_path / "src" / f"future{suffix}"
    module.write_bytes(b"static input")

    with package_source("future", source=tmp_path) as located:
        assert located.root == module


def test_source_module_wins_over_stub_and_native_fallback(tmp_path: Path) -> None:
    for suffix in (".py", ".pyi", ".so"):
        (tmp_path / f"future{suffix}").write_bytes(b"static input")

    with package_source("future", source=tmp_path) as located:
        assert located.root == tmp_path / "future.py"


def test_adjacent_stub_wins_over_opaque_native_extension(tmp_path: Path) -> None:
    (tmp_path / "future.pyi").write_text(
        "def compute() -> int: ...\n", encoding="utf-8"
    )
    (tmp_path / "future.abi3.so").write_bytes(b"native")

    with package_source("future", source=tmp_path) as located:
        assert located.root == tmp_path / "future.pyi"


def test_stub_package_and_dotted_module_are_discovered(tmp_path: Path) -> None:
    package = tmp_path / "future"
    package.mkdir()
    (package / "__init__.pyi").write_text("", encoding="utf-8")
    (package / "nested.pyi").write_text("class Thing: ...\n", encoding="utf-8")

    with package_source("future", source=tmp_path) as located:
        assert located.root == package
    with package_source("future.nested", source=tmp_path) as located:
        assert located.root == package / "nested.pyi"


def test_unrelated_native_names_are_never_selected(tmp_path: Path) -> None:
    (tmp_path / "future_helper.so").write_bytes(b"unrelated")
    (tmp_path / "other.future.so").write_bytes(b"unrelated")

    with (
        pytest.raises(ValueError, match="cannot find"),
        package_source("future", source=tmp_path),
    ):
        pytest.fail("an unrelated library was selected")


def test_concrete_module_wins_over_empty_namespace_directory(tmp_path: Path) -> None:
    (tmp_path / "future").mkdir()
    module = tmp_path / "future.py"
    module.write_text("", encoding="utf-8")

    with package_source("future", source=tmp_path) as located:
        assert located.root == module


def test_installed_dotted_module_uses_sys_path_without_parent_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "future"
    package.mkdir()
    (package / "__init__.py").write_text(
        "raise RuntimeError('parent must not execute')\n", encoding="utf-8"
    )
    module = package / "nested.py"
    module.write_text("class Entry: ...\n", encoding="utf-8")
    metadata = tmp_path / "future_sdk-1.2.3.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: Future.SDK\nVersion: 1.2.3\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        source_module.sys, "path", [str(tmp_path), *source_module.sys.path]
    )

    with package_source("future.nested", distribution="future-sdk") as located:
        assert located.root == module
        assert located.version == "1.2.3"


def test_installed_native_extension_is_located_without_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = tmp_path / "future.cpython-313-x86_64-linux-gnu.so"
    native.write_bytes(b"not a loadable native library")
    monkeypatch.setattr(
        source_module.sys, "path", [str(tmp_path), *source_module.sys.path]
    )

    with package_source("future") as located:
        assert located.root == native


def test_missing_filesystem_module_requests_static_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_module.sys, "path", [])

    with pytest.raises(ValueError, match="static stubs"), package_source("sys"):
        pytest.fail("a built-in module was imported")


def test_latest_wheel_uses_isolated_target_and_cleans_it_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[Path] = []

    def install(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        target = Path(command[command.index("--target") + 1])
        recorded.append(target)
        target.mkdir()
        (target / "future.pyi").write_text("class Entry: ...\n", encoding="utf-8")
        assert command[-1] == "future-sdk"
        assert {"--no-deps", "--only-binary=:all:", "--no-input"} <= set(command)
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(source_module.subprocess, "run", install)

    with package_source("future", latest=True, distribution="future-sdk") as located:
        assert located.root == recorded[0] / "future.pyi"
        assert located.root.exists()

    assert not recorded[0].parent.exists()


@pytest.mark.parametrize("failure", ("nonzero", "timeout", "missing_module"))
def test_latest_failures_preserve_diagnostics_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    recorded: list[Path] = []

    def install(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        target = Path(command[command.index("--target") + 1])
        recorded.append(target)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        target.mkdir()
        cast(BinaryIO, kwargs["stdout"]).write(b"controlled pip diagnostic")
        return subprocess.CompletedProcess(command, 1 if failure == "nonzero" else 0)

    monkeypatch.setattr(source_module.subprocess, "run", install)
    error_type = ValueError if failure == "missing_module" else RuntimeError
    expected = {
        "nonzero": "controlled pip diagnostic",
        "timeout": "exceeded",
        "missing_module": "does not contain",
    }[failure]

    with (
        pytest.raises(error_type, match=expected),
        package_source("future", latest=True),
    ):
        pytest.fail("a failed installation yielded source")

    assert not recorded[0].parent.exists()


@pytest.mark.parametrize(
    "distribution",
    ("--index-url=evil", "future[extra]", "https://example.com/x.whl", "x y"),
)
def test_distribution_injection_is_rejected_before_pip(
    monkeypatch: pytest.MonkeyPatch, distribution: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid distribution reached subprocess execution")

    monkeypatch.setattr(source_module.subprocess, "run", forbidden)

    with (
        pytest.raises(ValueError, match="plain package name"),
        package_source("future", latest=True, distribution=distribution),
    ):
        pytest.fail("invalid distribution was accepted")


@pytest.mark.parametrize("package", ("", "..future", "future/other", "future.-other"))
def test_invalid_python_import_names_are_rejected(package: str) -> None:
    with pytest.raises(ValueError, match="absolute dotted"), package_source(package):
        pytest.fail("invalid import name was accepted")


def test_latest_and_explicit_source_are_mutually_exclusive(tmp_path: Path) -> None:
    with (
        pytest.raises(ValueError, match="mutually exclusive"),
        package_source("future", source=tmp_path, latest=True),
    ):
        pytest.fail("conflicting source modes were accepted")


def test_install_timeout_must_be_positive() -> None:
    with (
        pytest.raises(ValueError, match="positive"),
        package_source("future", install_timeout=0),
    ):
        pytest.fail("nonpositive installation timeout was accepted")


def test_source_module_name_does_not_require_a_pypi_distribution_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "_module.py"
    path.write_text("def run(): pass\n", encoding="utf-8")
    with package_source("_module", source=path) as located:
        assert located.root == path
