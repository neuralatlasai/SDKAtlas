"""Exercise module entry-point semantics and malformed-only checkout failures."""

import runpy
import sys
from pathlib import Path

import pytest

from sdk_atlas.cli import main


def test_module_main_dispatches_help_and_returns_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["sdk-atlas", "--help"])
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("sdk_atlas", run_name="__main__")
    assert stopped.value.code == 0
    assert "without importing it" in capsys.readouterr().out


def test_importing_module_entrypoint_does_not_dispatch_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("entry-point import dispatched the CLI")

    monkeypatch.setattr("sdk_atlas.cli.main", forbidden)
    runpy.run_module("sdk_atlas", run_name="controlled_entrypoint_import")


def test_malformed_only_checkout_fails_without_publishing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "sample.py"
    path.write_text("def missing_body(\n", encoding="utf-8")
    output = tmp_path / "output"
    assert (
        main(["--package", "sample", "--source", str(path), "--out", str(output)]) == 1
    )
    assert "no readable Python modules found" in capsys.readouterr().err
    assert not output.exists()
