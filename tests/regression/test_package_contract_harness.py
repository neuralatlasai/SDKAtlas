"""Prove real-package gates reject missing method data and broken exports."""

import csv
import json
import runpy
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from .golden_support import contract_inventory

_HARNESS = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts" / "package_regressions.py")
)
_PACKAGE = _HARNESS["PackageContract"]
_METHOD = _HARNESS["MethodContract"]
_VALIDATE = _HARNESS["_validate_inventory"]


@pytest.fixture
def exported_contract(tmp_path: Path) -> tuple[Path, object]:
    output = contract_inventory(tmp_path)
    contract = _PACKAGE(
        package="contract_sdk",
        distribution="contract-sdk",
        version="contract-1",
        minimum_counts={"method_definitions": 15, "functions": 6},
        requires_native_diagnostic=False,
        methods=(
            _METHOD(
                class_id="contract_sdk.resources.Widgets",
                name="retrieve",
                return_annotation="Response",
                parameters=("identifier", "raw"),
                output_fields=("request_id", "payload", "status", "complete"),
                output_models=("contract_sdk.models.Envelope",),
                require_docstring=True,
                require_client_path=True,
            ),
        ),
    )
    return output, contract


def test_package_gate_accepts_complete_controlled_inventory(
    exported_contract: tuple[Path, object],
) -> None:
    output, contract = exported_contract
    report = _VALIDATE(output, contract, "pinned")
    assert report["method_csv_rows_checked"] == 16
    assert report["representative_variants_checked"] == 1


@pytest.mark.parametrize(
    ("regression", "message"),
    [
        ("method_output", "missing methods expected-output rows"),
        ("function_output", "missing functions expected-output rows"),
        ("response_field", "response field regression"),
        ("caller_parameter", "caller parameter regression"),
        ("method_csv", "methods.csv omits"),
        ("implicit_receiver", "implicit receiver leaked"),
        ("version", "pinned version changed"),
        ("coverage", "definition coverage regression"),
        ("artifact", "export artifact set changed"),
    ],
)
def test_package_gate_rejects_corrupted_semantic_evidence(
    regression: str, message: str, exported_contract: tuple[Path, object]
) -> None:
    output, contract = exported_contract
    if regression in {"method_output", "function_output"}:
        # Delete an unreferenced output row so the cardinality assertion, rather
        # than a foreign-key error, has to catch a dropped analysis record.
        table, key = (
            ("method_outputs", "method_id")
            if regression == "method_output"
            else ("function_outputs", "function_id")
        )
        with closing(sqlite3.connect(output / "inventory.sqlite")) as database:
            database.execute(
                f"DELETE FROM {table} WHERE {key} IN "
                f"(SELECT {key} FROM {table} "
                "WHERE output_json LIKE '%type_only%' LIMIT 1)"
            )
            database.commit()
    elif regression in {"response_field", "caller_parameter"}:
        with closing(sqlite3.connect(output / "inventory.sqlite")) as database:
            identifier = database.execute(
                "SELECT id FROM methods WHERE name='retrieve' "
                "AND return_annotation='Response'"
            ).fetchone()[0]
            if regression == "response_field":
                payload = json.loads(
                    database.execute(
                        "SELECT output_json FROM method_outputs WHERE method_id=?",
                        (identifier,),
                    ).fetchone()[0]
                )
                payload["fields"] = [
                    field for field in payload["fields"] if field["name"] != "payload"
                ]
                database.execute(
                    "UPDATE method_outputs SET output_json=? WHERE method_id=?",
                    (json.dumps(payload), identifier),
                )
            else:
                database.execute(
                    "DELETE FROM parameters WHERE method_id=? AND name='identifier'",
                    (identifier,),
                )
            database.commit()
    elif regression in {"method_csv", "implicit_receiver"}:
        path = output / "methods.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames
            rows = list(reader)
        assert columns is not None
        if regression == "method_csv":
            rows.pop()
        else:
            rows[0]["parameters"] = json.dumps([{"name": "self", "implicit": True}])
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
    elif regression in {"version", "coverage"}:
        path = output / "summary.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        if regression == "version":
            document["version"] = "wrong-wheel"
        else:
            document["counts"]["method_definitions"] = 1
        path.write_text(json.dumps(document), encoding="utf-8")
    else:
        (output / "functions.csv").unlink()

    with pytest.raises(ValueError, match=message):
        _VALIDATE(output, contract, "pinned")
