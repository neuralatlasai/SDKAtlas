"""Exercise filesystem trust boundaries with real links and traversal attempts.

POSIX CI must execute all link cases. Windows skips only when its symlink
privilege is unavailable; ordinary filesystem and name-validation cases still run.
Explicit source/output roots are user-selected canonical paths, not a sandbox.
Links inside discovered trees and owned publication filenames are the boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sdk_atlas.cli import main
from sdk_atlas.export import OUTPUT_FILES, write_inventory
from sdk_atlas.graph import resolve_inventory
from sdk_atlas.scanner import scan_source
from sdk_atlas.source import package_source


def _link(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", 0) == 1314:
            pytest.skip("Windows symlink privilege unavailable; exercised on POSIX CI")
        raise


def test_discovery_does_not_read_linked_files_directories_or_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sample"
    root.mkdir()
    (root / "__init__.py").write_text("def visible() -> int: ...\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "secret.py"
    sentinel.write_text("def forbidden() -> str: ...\n", encoding="utf-8")
    _link(root / "leaked.py", sentinel)
    _link(root / "foreign", outside, directory=True)
    _link(root / "cycle", root, directory=True)
    _link(root / "missing.py", outside / "absent.py")
    original_open = Path.open

    def guarded_open(path: Path, *args: object, **kwargs: object) -> object:
        assert path.resolve() != sentinel, "scanner read outside through a symlink"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    source = scan_source(root, "sample", max_files=1)
    assert [module.name for module in source.modules] == ["sample"]
    assert [function.name for function in source.functions] == ["visible"]
    assert source.issues == ()


@pytest.mark.parametrize("kind", ["file", "dangling", "directory"])
def test_publication_rejects_owned_artifact_links_without_touching_targets(
    tmp_path: Path, kind: str
) -> None:
    module = tmp_path / "sample.py"
    module.write_text("def visible() -> int: ...\n", encoding="utf-8")
    inventory = resolve_inventory(scan_source(module, "sample"))
    outside = tmp_path / "outside"
    if kind == "file":
        outside.write_bytes(b"private original")
    elif kind == "directory":
        outside.mkdir()
        (outside / "notes.txt").write_bytes(b"private original")
    output = tmp_path / "output"
    output.mkdir()
    (output / "notes.txt").write_bytes(b"unrelated")
    target = output / "methods.csv"
    _link(target, outside, directory=kind == "directory")
    with pytest.raises(FileExistsError, match="regular file"):
        write_inventory(inventory, output)
    assert target.is_symlink()
    assert set(path.name for path in output.iterdir()) == {"notes.txt", "methods.csv"}
    assert (output / "notes.txt").read_bytes() == b"unrelated"
    if kind == "file":
        assert outside.read_bytes() == b"private original"
    elif kind == "directory":
        assert (outside / "notes.txt").read_bytes() == b"private original"
    else:
        assert not outside.exists()
    assert not list(tmp_path.glob(".sdk-atlas-*"))


def test_artifact_link_inserted_during_generation_aborts_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Preflight validation alone is insufficient; the second validation must
    # detect an injected link before any owned output file is moved.
    from sdk_atlas import export

    module = tmp_path / "sample.py"
    module.write_text("def visible(): ...\n", encoding="utf-8")
    inventory = resolve_inventory(scan_source(module, "sample"))
    output = tmp_path / "output"
    output.mkdir()
    original = output / "api_methods.csv"
    original.write_bytes(b"previous export")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"do not replace")
    # Verify privilege before generation rather than hiding a skip in a callback.
    probe = tmp_path / "probe"
    _link(probe, outside)
    probe.unlink()
    generate = export._write_artifacts

    def inject_link(records: object, directory: Path) -> None:
        generate(records, directory)
        _link(output / "parameters.csv", outside)

    monkeypatch.setattr(export, "_write_artifacts", inject_link)
    with pytest.raises(FileExistsError, match="regular file"):
        write_inventory(inventory, output)
    assert original.read_bytes() == b"previous export"
    assert outside.read_bytes() == b"do not replace"
    assert not list(tmp_path.glob(".sdk-atlas-*"))


@pytest.mark.parametrize(
    "package",
    [
        "../outside",
        "..outside",
        "sample/../../outside",
        "sample\\outside",
        "C:\\outside",
        "/outside",
        "sample.\x00outside",
        ".",
        "sample..outside",
    ],
)
def test_import_name_traversal_is_rejected_before_filesystem_or_process_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: str
) -> None:
    from sdk_atlas import source

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid import name crossed the filesystem/process boundary")

    monkeypatch.setattr(source, "_checkout_root", forbidden)
    monkeypatch.setattr(source.subprocess, "run", forbidden)
    with (
        pytest.raises(ValueError, match="absolute dotted"),
        package_source(package, source=tmp_path),
    ):
        pytest.fail("path-like import name accepted")
    with (
        pytest.raises(ValueError, match="absolute dotted"),
        package_source(package, latest=True),
    ):
        pytest.fail("path-like import name accepted for installation")


def test_relative_import_traversal_is_symbolic_and_never_opens_external_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sample"
    root.mkdir()
    (root / "__init__.py").write_text(
        "from ...outside import Hidden\n"
        "class Visible(Hidden):\n    def call(self): ...\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.py"
    outside.write_text("def secret(): ...\n", encoding="utf-8")
    output = tmp_path / "output"
    assert (
        main(["--package", "sample", "--source", str(root), "--out", str(output)]) == 0
    )
    assert {path.name for path in output.iterdir()} == set(OUTPUT_FILES)
    assert outside.read_text(encoding="utf-8") == "def secret(): ...\n"
    assert "secret" not in (output / "functions.csv").read_text(encoding="utf-8")


def test_output_file_conflict_leaves_existing_file_unchanged(tmp_path: Path) -> None:
    module = tmp_path / "sample.py"
    module.write_text("def visible(): ...\n", encoding="utf-8")
    output = tmp_path / "output"
    output.write_bytes(b"user file")
    with pytest.raises(NotADirectoryError):
        write_inventory(resolve_inventory(scan_source(module, "sample")), output)
    assert output.read_bytes() == b"user file"
