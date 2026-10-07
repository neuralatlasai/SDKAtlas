"""Verify pinned wheels or the real --latest CLI against reviewed SDK contracts.

Examples::

    python scripts/package_regressions.py --mode pinned --package openai
    python scripts/package_regressions.py --mode latest --package agents

Network acquisition is deliberate and confined to this integration harness.
Pinned mode obtains exactly the reviewed wheel without dependency installation;
latest mode invokes the production CLI's live package-index path end to end.
Neither mode imports target packages or needs provider credentials. Temporary
targets and large inventories are deleted on success and failure. A compact
--report JSON can be retained as CI evidence. Source-dependent minimum counts
are reviewed lower bounds, not claims of complete runtime API reconstruction.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from sdk_atlas.export import OUTPUT_FILES

_CONTRACTS = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "package_contracts.json"
)
_MAX_JSON_BYTES = 1024 * 1024
_MAX_CSV_CELL_BYTES = 16 * 1024 * 1024
_PROCESS_TIMEOUT = 300


@dataclass(frozen=True, slots=True)
class MethodContract:
    class_id: str
    name: str
    return_annotation: str
    parameters: tuple[str, ...]
    output_fields: tuple[str, ...]
    output_models: tuple[str, ...]
    require_docstring: bool
    require_client_path: bool


@dataclass(frozen=True, slots=True)
class PackageContract:
    package: str
    distribution: str
    version: str
    minimum_counts: dict[str, int]
    requires_native_diagnostic: bool
    methods: tuple[MethodContract, ...]


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("contract JSON must contain objects with string keys")
    return dict(value)


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("contract JSON must contain arrays")
    return list(value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("contract JSON must contain strings")
    return value


def _strings(value: object) -> tuple[str, ...]:
    return tuple(_string(item) for item in _sequence(value))


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("contract JSON must contain booleans")
    return value


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("inventory counts must be nonnegative integers")
    return value


def _read_json(path: Path) -> object:
    with path.open("rb") as handle:
        content = handle.read(_MAX_JSON_BYTES + 1)
    if len(content) > _MAX_JSON_BYTES:
        raise ValueError(f"JSON input exceeds {_MAX_JSON_BYTES} bytes: {path}")
    return json.loads(content)


def _load_contracts() -> tuple[PackageContract, ...]:
    document = _mapping(_read_json(_CONTRACTS))
    if document.get("schema_version") != 1:
        raise ValueError("unsupported package regression contract schema")
    contracts: list[PackageContract] = []
    for entry in _sequence(document["packages"]):
        data = _mapping(entry)
        counts: dict[str, int] = {}
        for key, value in _mapping(data["minimum_counts"]).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"minimum count must be a positive integer: {key}")
            counts[key] = value
        methods: list[MethodContract] = []
        for method_entry in _sequence(data["methods"]):
            method = _mapping(method_entry)
            methods.append(
                MethodContract(
                    class_id=_string(method["class_id"]),
                    name=_string(method["name"]),
                    return_annotation=_string(method["return_annotation"]),
                    parameters=_strings(method["parameters"]),
                    output_fields=_strings(method["output_fields"]),
                    output_models=_strings(method["output_models"]),
                    require_docstring=_boolean(method["require_docstring"]),
                    require_client_path=_boolean(method["require_client_path"]),
                )
            )
        contract = PackageContract(
            package=_string(data["package"]),
            distribution=_string(data["distribution"]),
            version=_string(data["version"]),
            minimum_counts=counts,
            requires_native_diagnostic=_boolean(data["requires_native_diagnostic"]),
            methods=tuple(methods),
        )
        if not all(part.isidentifier() for part in contract.package.split(".")):
            raise ValueError("invalid package import name in regression contract")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", contract.distribution):
            raise ValueError("invalid distribution name in regression contract")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", contract.version):
            raise ValueError("invalid pinned version in regression contract")
        if not contract.methods:
            raise ValueError("every package requires representative method contracts")
        contracts.append(contract)
    if len({contract.package for contract in contracts}) != len(contracts):
        raise ValueError("duplicate package regression contract")
    return tuple(contracts)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _run(command: Sequence[str], log_path: Path) -> None:
    # Capture large pip/diagnostic output on disk rather than retaining all of
    # NumPy/OpenAI diagnostics in parent-process memory. Bound failure excerpts.
    with log_path.open("w+b") as log:
        completed = subprocess.run(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=_PROCESS_TIMEOUT,
            check=False,
        )
        if completed.returncode:
            log.seek(0)
            detail = log.read(8192).decode("utf-8", errors="replace")
            raise ValueError(f"subprocess failed ({completed.returncode}): {detail}")


def _method_contract(database: sqlite3.Connection, contract: MethodContract) -> int:
    rows = database.execute(
        "SELECT id, docstring FROM methods "
        "WHERE class_id=? AND name=? AND return_annotation=?",
        (contract.class_id, contract.name, contract.return_annotation),
    ).fetchall()
    label = f"{contract.class_id}.{contract.name} -> {contract.return_annotation}"
    _require(bool(rows), f"representative method missing: {label}")
    for identifier, docstring in rows:
        parameters = {
            name
            for (name,) in database.execute(
                "SELECT name FROM parameters WHERE method_id=? AND implicit=0",
                (identifier,),
            )
        }
        _require(
            set(contract.parameters) <= parameters,
            f"caller parameter regression: {identifier}: {parameters}",
        )
        output_row = database.execute(
            "SELECT output_json FROM method_outputs WHERE method_id=?", (identifier,)
        ).fetchone()
        _require(output_row is not None, f"method output missing: {identifier}")
        output = _mapping(json.loads(output_row[0]))
        fields = {
            _string(_mapping(field)["name"]) for field in _sequence(output["fields"])
        }
        _require(
            set(contract.output_fields) <= fields,
            f"response field regression: {identifier}: {fields}",
        )
        _require(
            set(contract.output_models) <= set(_strings(output["model_ids"])),
            f"response model regression: {identifier}",
        )
        if contract.require_docstring:
            _require(bool(docstring.strip()), f"docstring missing: {identifier}")
        if contract.require_client_path:
            _require(
                database.execute(
                    "SELECT 1 FROM api_methods WHERE method_id=? LIMIT 1", (identifier,)
                ).fetchone()
                is not None,
                f"client path missing: {identifier}",
            )
    return len(rows)


def _validate_inventory(
    output: Path, contract: PackageContract, mode: str
) -> dict[str, object]:
    _require(
        {path.name for path in output.iterdir()} == set(OUTPUT_FILES),
        "export artifact set changed or publication is incomplete",
    )
    report = _mapping(_read_json(output / "summary.json"))
    _require(report["package"] == contract.package, "incorrect target package")
    version = _string(report["version"])
    _require(version not in {"", "unknown"}, "wheel metadata version is missing")
    if mode == "pinned":
        _require(version == contract.version, f"pinned version changed: {version}")
    else:
        _require(
            not Path(_string(report["source_root"])).exists(),
            "--latest temporary installation was not cleaned up after CLI exit",
        )
    counts = {name: _count(value) for name, value in _mapping(report["counts"]).items()}
    for name, minimum in contract.minimum_counts.items():
        actual = counts[name]
        _require(
            actual >= minimum,
            f"definition coverage regression: {name}={actual}, minimum={minimum}",
        )
    _require(
        counts["methods_with_output_analysis"] == counts["method_definitions"],
        "some retained methods have no expected-output analysis",
    )
    _require(
        counts["output_records"] == counts["method_definitions"] + counts["functions"],
        "function/method output cardinality mismatch",
    )
    if contract.requires_native_diagnostic:
        _require(
            any(
                _mapping(issue)["kind"] == "native_module"
                for issue in _sequence(report["issues"])
            ),
            "compiled wheel did not retain native-module evidence",
        )
    with closing(sqlite3.connect(output / "inventory.sqlite")) as database:
        _require(
            database.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
            "SQLite integrity check failed",
        )
        _require(
            database.execute("PRAGMA foreign_key_check").fetchall() == [],
            "SQLite foreign keys reference missing definitions",
        )
        for definitions, outputs, key in (
            ("methods", "method_outputs", "method_id"),
            ("functions", "function_outputs", "function_id"),
        ):
            # Table/column identifiers are fixed harness constants; values
            # selected from SDK source are always bound SQL parameters.
            missing = database.execute(
                f"SELECT COUNT(*) FROM {definitions} AS d "
                f"LEFT JOIN {outputs} AS o ON o.{key}=d.id WHERE o.{key} IS NULL"
            ).fetchone()[0]
            _require(missing == 0, f"missing {definitions} expected-output rows")
        checked = sum(_method_contract(database, method) for method in contract.methods)
        method_ids = {
            identifier for (identifier,) in database.execute("SELECT id FROM methods")
        }
    previous_limit = csv.field_size_limit(_MAX_CSV_CELL_BYTES)
    csv_ids: set[str] = set()
    try:
        with (output / "methods.csv").open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                identifier = row["id"]
                _require(
                    identifier not in csv_ids, f"duplicate CSV method: {identifier}"
                )
                csv_ids.add(identifier)
                _require(
                    _mapping(json.loads(row["expected_output"]))["callable_id"]
                    == identifier,
                    f"CSV output joined to wrong definition: {identifier}",
                )
                _require(
                    all(
                        _mapping(parameter)["implicit"] is False
                        for parameter in _sequence(json.loads(row["parameters"]))
                    ),
                    f"implicit receiver leaked into caller parameters: {identifier}",
                )
    finally:
        csv.field_size_limit(previous_limit)
    _require(csv_ids == method_ids, "methods.csv omits retained SQLite definitions")
    return {
        "package": contract.package,
        "distribution": contract.distribution,
        "version": version,
        "mode": mode,
        "counts": counts,
        "representative_variants_checked": checked,
        "method_csv_rows_checked": len(csv_ids),
        "sqlite_integrity": "ok",
        "foreign_key_violations": 0,
    }


def _package_regression(contract: PackageContract, mode: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(
        prefix="sdk-atlas-package-regression-"
    ) as temporary:
        directory = Path(temporary)
        output = directory / "inventory"
        command = [
            sys.executable,
            "-m",
            "sdk_atlas",
            "--package",
            contract.package,
            "--distribution",
            contract.distribution,
            "--out",
            str(output),
        ]
        if mode == "pinned":
            target = directory / "target"
            _run(
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
                    f"{contract.distribution}=={contract.version}",
                ],
                directory / "pip.log",
            )
            command.extend(("--source", str(target)))
        else:
            command.append("--latest")
        _run(command, directory / "scan.log")
        return _validate_inventory(output, contract, mode)


def main(argv: Sequence[str] | None = None) -> int:
    """Run selected live integration contracts; failures return process status 1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("pinned", "latest"))
    parser.add_argument(
        "--package", action="append", default=[], help="import name; repeatable"
    )
    parser.add_argument("--report", type=Path, help="retain compact JSON CI evidence")
    arguments = parser.parse_args(argv)
    try:
        contracts = _load_contracts()
        selected = set(arguments.package)
        unknown = selected - {contract.package for contract in contracts}
        _require(not unknown, f"unknown package contracts: {sorted(unknown)}")
        results = [
            _package_regression(contract, arguments.mode)
            for contract in contracts
            if not selected or contract.package in selected
        ]
        report = json.dumps({"results": results}, indent=2, ensure_ascii=False) + "\n"
        if arguments.report is not None:
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            arguments.report.write_text(report, encoding="utf-8", newline="\n")
        print(report, end="")
    except (
        OSError,
        ValueError,
        KeyError,
        sqlite3.Error,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"package regression failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
