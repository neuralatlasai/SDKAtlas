"""Exercise opt-in static dependencies without imports or package installation."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from sdk_atlas import cli as cli_module
from sdk_atlas import source as source_module
from sdk_atlas.cli import main
from sdk_atlas.models import Inventory
from sdk_atlas.source import PackageSource


def _packages(tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "target"
    dependency = tmp_path / "dependencies" / "static_dependency"
    target.mkdir()
    dependency.mkdir(parents=True)
    (target / "__init__.py").write_text(
        """
raise RuntimeError("target imports must not execute")
from static_dependency import Base as Parent
class Target(Parent):
    def own(self):
        return 1
""",
        encoding="utf-8",
    )
    (dependency / "__init__.py").write_text(
        """
raise RuntimeError("dependency imports must not execute")
class Base:
    def inherited(self):
        return 2
""",
        encoding="utf-8",
    )
    return target, dependency


def _rows(output: Path) -> list[tuple[str, str, int]]:
    with closing(sqlite3.connect(output / "inventory.sqlite")) as database:
        return database.execute(
            "SELECT root_class, method_name, inherited FROM api_methods "
            "ORDER BY method_name"
        ).fetchall()


def test_dependency_source_adds_inherited_methods_without_support_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dependency = _packages(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("a static dependency scan attempted package installation")

    monkeypatch.setattr(source_module.subprocess, "run", forbidden)
    output = tmp_path / "inventory"
    assert (
        main(
            [
                "--package",
                "target",
                "--source",
                str(target),
                "--dependency-source",
                f"static_dependency={dependency}",
                "--out",
                str(output),
            ]
        )
        == 0
    )

    assert _rows(output) == [
        ("target.Target", "inherited", 1),
        ("target.Target", "own", 0),
    ]
    assert "target" not in sys.modules
    assert "static_dependency" not in sys.modules


def test_default_scan_does_not_follow_or_scan_installed_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dependency = _packages(tmp_path)
    monkeypatch.syspath_prepend(str(dependency.parent))
    output = tmp_path / "inventory"

    assert (
        main(["--package", "target", "--source", str(target), "--out", str(output)])
        == 0
    )
    assert _rows(output) == [("target.Target", "own", 0)]
    with closing(sqlite3.connect(output / "inventory.sqlite")) as database:
        assert database.execute("SELECT name FROM modules").fetchall() == [("target",)]


def test_installed_dependency_is_discovered_without_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dependency = _packages(tmp_path)
    monkeypatch.syspath_prepend(str(dependency.parent))
    output = tmp_path / "inventory"

    assert (
        main(
            [
                "--package",
                "target",
                "--source",
                str(target),
                "--dependency",
                "static_dependency",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert ("target.Target", "inherited", 1) in _rows(output)
    assert "static_dependency" not in sys.modules


@pytest.mark.parametrize("mapping", ["external", "external=", "=path", "x-y=path"])
def test_invalid_dependency_mapping_fails_before_any_source_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapping: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid mapping reached source acquisition")

    monkeypatch.setattr(cli_module, "package_source", forbidden)
    output = tmp_path / "inventory"

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--package",
                "target",
                "--latest",
                "--dependency-source",
                mapping,
                "--out",
                str(output),
            ]
        )

    assert error.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("mode", ["--dependency", "--dependency-source"])
def test_missing_dependency_fails_without_creating_output(
    tmp_path: Path, mode: str, capsys: pytest.CaptureFixture[str]
) -> None:
    target, _ = _packages(tmp_path)
    output = tmp_path / "inventory"
    missing = "sdk_atlas_absent_dependency_fixture"
    value = missing if mode == "--dependency" else f"{missing}={tmp_path / 'missing'}"

    assert (
        main(
            [
                "--package",
                "target",
                "--source",
                str(target),
                mode,
                value,
                "--out",
                str(output),
            ]
        )
        == 1
    )
    assert "sdk-atlas:" in capsys.readouterr().err
    assert not output.exists()


def test_duplicate_dependencies_preserve_an_existing_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target, dependency = _packages(tmp_path)
    output = tmp_path / "inventory"
    output.mkdir()
    existing = output / "keep.txt"
    existing.write_text("prior output", encoding="utf-8")

    assert (
        main(
            [
                "--package",
                "target",
                "--source",
                str(target),
                "--dependency-source",
                f"static_dependency={dependency}",
                "--dependency-source",
                f"static_dependency={dependency}",
                "--out",
                str(output),
            ]
        )
        == 1
    )
    assert "duplicate source package namespace" in capsys.readouterr().err
    assert list(output.iterdir()) == [existing]
    assert existing.read_text("utf-8") == "prior output"


def test_dependency_sources_receive_primary_source_byte_limit(tmp_path: Path) -> None:
    target, dependency = _packages(tmp_path)
    (dependency / "__init__.py").write_text("VALUE = " + "1" * 1024, encoding="utf-8")
    output = tmp_path / "inventory"

    assert (
        main(
            [
                "--package",
                "target",
                "--source",
                str(target),
                "--dependency-source",
                f"static_dependency={dependency}",
                "--max-source-bytes",
                "512",
                "--out",
                str(output),
            ]
        )
        == 1
    )
    assert not output.exists()


def test_latest_applies_only_to_target_and_dependency_context_closes_before_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target, dependency = _packages(tmp_path)
    output = tmp_path / "inventory"
    calls: list[tuple[str, object]] = []
    active: set[str] = set()
    original_write = cli_module.write_inventory

    @contextmanager
    def acquire(package: str, **kwargs: object) -> Iterator[PackageSource]:
        calls.append((package, kwargs.get("latest", False)))
        active.add(package)
        try:
            yield PackageSource(target if package == "target" else dependency)
        finally:
            active.remove(package)

    def export(inventory: Inventory, destination: Path) -> None:
        assert active == {"target"}
        original_write(inventory, destination)

    monkeypatch.setattr(cli_module, "package_source", acquire)
    monkeypatch.setattr(cli_module, "write_inventory", export)

    assert (
        main(
            [
                "--package",
                "target",
                "--latest",
                "--dependency",
                "static_dependency",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert calls == [("target", True), ("static_dependency", False)]
    assert active == set()
