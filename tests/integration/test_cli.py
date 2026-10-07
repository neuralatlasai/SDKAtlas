"""Exercise the installed command boundary against arbitrary module shapes."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from sdk_atlas.cli import main
from sdk_atlas.export import OUTPUT_FILES


@pytest.mark.parametrize(
    "package",
    ["numpy", "vllm", "transformers", "anthropic", "agents", "unseen_future_sdk"],
)
def test_vendor_independent_source_scan(
    package: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "checkout" / "src" / package
    root.mkdir(parents=True)
    # Scanning must succeed without executing this deliberately hostile import.
    (root / "__init__.py").write_text(
        """
raise RuntimeError("target import must never execute")
from .client import Client
from .ops import calculate
VERSION: str = "future"
""",
        encoding="utf-8",
    )
    (root / "client.py").write_text(
        """
class Resource:
    def create(this, value):
        return this._post(f"/future/{value}")
class Client:
    def __init__(this):
        this.unknown = Resource()
""",
        encoding="utf-8",
    )
    (root / "ops.py").write_text(
        "def calculate(value: int, /) -> int:\n    return value * 2\n", encoding="utf-8"
    )
    output = tmp_path / "output"
    assert (
        main(
            [
                "--package",
                package,
                "--source",
                str(tmp_path / "checkout"),
                "--out",
                str(output),
                "--print-summary",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["package"] == package
    with closing(sqlite3.connect(output / "inventory.sqlite")) as database:
        paths = database.execute("SELECT client_path FROM api_methods").fetchall()
        assert ("client.unknown.create",) in paths
        assert database.execute("SELECT name FROM functions").fetchall() == [
            ("calculate",)
        ]
    assert {path.name for path in output.iterdir()} == set(OUTPUT_FILES)


def test_module_entrypoint_reports_missing_source_without_traceback(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sdk_atlas",
            "--package",
            "uninstalled_test_module",
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "use --source or --latest" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "out").exists()


def test_package_is_explicit_and_positive_limits_are_enforced(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--out", str(tmp_path)])
    assert error.value.code == 2
    with pytest.raises(SystemExit) as error:
        main(["--package", "sample", "--out", str(tmp_path), "--max-paths", "0"])
    assert error.value.code == 2


def test_sqlite_export_failure_reports_actionable_process_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("def calculate() -> int: ...\n", encoding="utf-8")

    def fail_export(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr("sdk_atlas.cli.write_inventory", fail_export)
    assert (
        main(
            [
                "--package",
                "sample",
                "--source",
                str(source),
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == 1
    )
    assert "database or disk is full" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
