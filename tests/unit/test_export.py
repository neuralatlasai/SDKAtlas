"""Verify export schema, streaming records, and failure rollback."""

import csv
import json
import os
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from sdk_atlas import export
from sdk_atlas.models import (
    ApiMethod,
    CallableOutput,
    ClassField,
    ClassRecord,
    DynamicMember,
    FunctionRecord,
    GraphEdge,
    ImportBinding,
    Inventory,
    MethodRecord,
    ModuleMember,
    ModuleRecord,
    OutputField,
    Parameter,
    ScanIssue,
    SourceInventory,
    SourceReference,
    TransportEvidence,
)


@pytest.fixture
def inventory() -> Inventory:
    """Provide linked evidence with quoting, Unicode, and duplicate diagnostics."""
    root = "synthetic.Client"
    resource = "synthetic.Widgets"
    method_id = resource + ".create:10"
    evidence = TransportEvidence(
        id=method_id + ":transport:0",
        method_id=method_id,
        class_id=resource,
        module="synthetic",
        file="__init__.py",
        line=12,
        call="self._post",
        operation="POST",
        route="/widgets/{name}",
        route_kind="formatted",
    )
    method = MethodRecord(
        id=method_id,
        class_id=resource,
        name="create",
        signature="(self, *, name: str, tags: list[str] = []) -> Widget",
        return_annotation="Widget",
        parameters=(
            Parameter("name", "KEYWORD_ONLY", "str"),
            Parameter("tags", "KEYWORD_ONLY", "list[str]", "[]", False),
        ),
        decorators=("overload",),
        docstring='Create "café", safely.\nSecond line.',
        file="__init__.py",
        line=10,
        end_line=12,
        is_async=False,
        is_property=False,
        is_overload=True,
        returns=("Widget()",),
        delegates=("self.retrieve",),
        transport=(evidence,),
    )
    issue = ScanIssue("unresolved_base", "Unknown base", "__init__.py", 3)
    source = SourceInventory(
        package="synthetic",
        root="/source/synthetic",
        modules=(
            ModuleRecord(
                "synthetic",
                "__init__.py",
                True,
                (ImportBinding("Widget", "synthetic.types.Widget"),),
                ("Client",),
            ),
        ),
        classes=(
            ClassRecord(
                root,
                "synthetic",
                "Client",
                "Client",
                (),
                (),
                "Client docs",
                "__init__.py",
                1,
            ),
            ClassRecord(
                resource,
                "synthetic",
                "Widgets",
                "Widgets",
                ("Base",),
                ("resource",),
                "Resource docs",
                "__init__.py",
                3,
            ),
        ),
        methods=(method,),
        dynamic_members=(
            DynamicMember(
                root + ".widgets:2",
                root,
                "widgets",
                "Widgets(self)",
                "Widgets",
                ("Widgets",),
                "__init__.py",
                2,
            ),
        ),
        issues=(issue,),
        version="2.0.0",
    )
    return Inventory(
        source,
        (
            ApiMethod(
                "client.widgets.create:10",
                root,
                resource,
                resource,
                "client.widgets.create",
                "create",
                method_id,
                "transport",
                False,
            ),
        ),
        (
            GraphEdge(
                "client.widgets",
                root,
                root,
                resource,
                "client",
                "client.widgets",
                "widgets",
                "dynamic_member",
                "__init__.py",
                2,
            ),
        ),
        (issue,),
    )


def test_exports_all_artifacts_and_roundtrips_method_evidence(
    tmp_path: Path, inventory: Inventory
) -> None:
    output = export.write_inventory(inventory, tmp_path / "inventory")

    assert output == (tmp_path / "inventory").resolve()
    assert {path.name for path in output.iterdir()} == set(export.OUTPUT_FILES)
    with (output / "api_methods.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["docstring"] == inventory.source.methods[0].docstring
    assert rows[0]["method_kind"] == "transport"
    assert rows[0]["inherited"] == "false"

    records = [
        json.loads(line)
        for line in (output / "inventory.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(records) == 1
    assert records[0]["id"] == inventory.api_methods[0].id
    assert records[0]["method_id"] == inventory.source.methods[0].id
    assert records[0]["parameters"][1]["default"] == "[]"
    assert records[0]["transport"][0]["route"] == "/widgets/{name}"
    result = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert result == export.summary(inventory)
    assert result["counts"]["issues"] == 1
    assert result["counts"]["parameters"] == 2


def test_sqlite_normalizes_linked_source_and_resolved_paths(
    tmp_path: Path, inventory: Inventory
) -> None:
    output = export.write_inventory(inventory, tmp_path / "inventory")
    connection = sqlite3.connect(output / "inventory.sqlite")
    try:
        row = connection.execute(
            "SELECT api.client_path, parameter.name, "
            "evidence.operation, evidence.route "
            "FROM api_methods AS api "
            "JOIN parameters AS parameter ON parameter.method_id = api.method_id "
            "JOIN transport_evidence AS evidence ON evidence.method_id = api.method_id "
            "WHERE parameter.position = 0"
        ).fetchone()
        assert row == ("client.widgets.create", "name", "POST", "/widgets/{name}")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT expression FROM class_bases").fetchall() == [
            ("Base",)
        ]
        assert connection.execute("SELECT target FROM module_imports").fetchall() == [
            ("synthetic.types.Widget",)
        ]
        assert connection.execute("SELECT COUNT(*) FROM issues").fetchone() == (1,)
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM methods")
    finally:
        connection.close()


def test_empty_inventory_still_has_csv_headers_and_database(tmp_path: Path) -> None:
    inventory = Inventory(SourceInventory("empty", "/empty", (), (), (), ()), (), ())
    output = export.write_inventory(inventory, tmp_path / "inventory")

    for filename in export.OUTPUT_FILES:
        if filename.endswith(".csv"):
            with (output / filename).open(encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            assert len(rows) == 1
            assert rows[0]
    assert (output / "inventory.jsonl").read_bytes() == b""
    assert export.summary(inventory)["counts"] == {
        "modules": 0,
        "functions": 0,
        "function_parameters": 0,
        "function_transport": 0,
        "module_members": 0,
        "classes": 0,
        "class_fields": 0,
        "output_records": 0,
        "unknown_outputs": 0,
        "partial_outputs": 0,
        "method_definitions": 0,
        "methods_with_api_path": 0,
        "methods_without_api_path": 0,
        "methods_with_output_analysis": 0,
        "constructor_protocol_definitions": 0,
        "api_methods": 0,
        "parameters": 0,
        "resource_graph": 0,
        "transport_evidence": 0,
        "dynamic_members": 0,
        "issues": 0,
    }


def test_repeated_export_is_deterministic_and_preserves_unrelated_files(
    tmp_path: Path, inventory: Inventory
) -> None:
    output = export.write_inventory(inventory, tmp_path / "inventory")
    before = {name: (output / name).read_bytes() for name in export.OUTPUT_FILES}
    unrelated = output / "notes.txt"
    unrelated.write_text("User notes", encoding="utf-8")

    export.write_inventory(inventory, output)

    assert before == {
        name: (output / name).read_bytes() for name in export.OUTPUT_FILES
    }
    assert unrelated.read_text(encoding="utf-8") == "User notes"
    assert not list(tmp_path.glob(".sdk-atlas-*"))


def test_generation_failure_keeps_old_files_and_creates_no_new_output(
    tmp_path: Path, inventory: Inventory, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "api_methods.csv").write_bytes(b"previous export")
    (output / "notes.txt").write_bytes(b"unrelated")

    def fail_generation(unused_inventory: Inventory, unused_path: Path) -> None:
        raise sqlite3.OperationalError("Injected database failure")

    monkeypatch.setattr(export, "_write_sqlite", fail_generation)
    with pytest.raises(sqlite3.OperationalError, match="Injected"):
        export.write_inventory(inventory, output)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == {
        "api_methods.csv": b"previous export",
        "notes.txt": b"unrelated",
    }
    with pytest.raises(sqlite3.OperationalError):
        export.write_inventory(inventory, tmp_path / "new")
    assert not (tmp_path / "new").exists()
    assert not list(tmp_path.glob(".sdk-atlas-*"))


def test_failed_publication_restores_originals_and_removes_new_artifacts(
    tmp_path: Path, inventory: Inventory, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "inventory"
    output.mkdir()
    (output / "api_methods.csv").write_bytes(b"previous methods")
    (output / "resource_graph.csv").write_bytes(b"previous graph")
    (output / "notes.txt").write_bytes(b"unrelated")
    original_replace = os.replace

    def fail_one_replace(source: Path, target: Path) -> None:
        if source.parent.name == "artifacts" and source.name == "resource_graph.csv":
            raise PermissionError("Injected publication failure")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_one_replace)
    with pytest.raises(PermissionError, match="Injected publication failure"):
        export.write_inventory(inventory, output)

    assert {path.name: path.read_bytes() for path in output.iterdir()} == {
        "api_methods.csv": b"previous methods",
        "resource_graph.csv": b"previous graph",
        "notes.txt": b"unrelated",
    }
    assert not list(tmp_path.glob(".sdk-atlas-*"))


def test_failed_rollback_retains_original_backup(
    tmp_path: Path, inventory: Inventory, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "inventory"
    output.mkdir()
    (output / "api_methods.csv").write_bytes(b"previous methods")
    original_replace = os.replace

    def fail_publication_and_restore(source: Path, target: Path) -> None:
        if source.parent.name == "backups":
            raise PermissionError("Injected rollback failure")
        if source.parent.name == "artifacts" and source.name == "parameters.csv":
            raise PermissionError("Injected publication failure")
        original_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_publication_and_restore)
    with pytest.raises(export.ExportRecoveryError, match="originals retained"):
        export.write_inventory(inventory, output)
    staging = list(tmp_path.glob(".sdk-atlas-*"))
    assert len(staging) == 1
    assert (
        staging[0] / "backups" / "api_methods.csv"
    ).read_bytes() == b"previous methods"


def test_invalid_target_is_rejected_before_mutating_directory(
    tmp_path: Path, inventory: Inventory
) -> None:
    output = tmp_path / "inventory"
    conflict = output / "parameters.csv"
    conflict.mkdir(parents=True)
    (conflict / "notes.txt").write_bytes(b"User directory")

    with pytest.raises(FileExistsError, match="regular file"):
        export.write_inventory(inventory, output)
    assert list(output.iterdir()) == [conflict]
    assert (conflict / "notes.txt").read_bytes() == b"User directory"


def test_dangling_graph_reference_does_not_publish_partial_results(
    tmp_path: Path, inventory: Inventory
) -> None:
    invalid = replace(
        inventory,
        resource_graph=(replace(inventory.resource_graph[0], target_class="missing"),),
    )

    with pytest.raises(sqlite3.IntegrityError):
        export.write_inventory(invalid, tmp_path / "inventory")
    assert not (tmp_path / "inventory").exists()
    assert not list(tmp_path.glob(".sdk-atlas-*"))


@pytest.fixture
def module_inventory() -> Inventory:
    """Provide a NumPy-style stub surface without any client/resource graph."""
    function = FunctionRecord(
        id="numeric.mean:3",
        module="numeric",
        name="mean",
        signature="(a: ArrayLike, /, *, axis: int | None = None) -> float",
        return_annotation="float",
        parameters=(
            Parameter("a", "POSITIONAL_ONLY", "ArrayLike"),
            Parameter("axis", "KEYWORD_ONLY", "int | None", "None", False),
        ),
        decorators=("overload",),
        docstring="Return the mean of the supplied array.",
        file="__init__.pyi",
        line=3,
        end_line=3,
        is_async=False,
        is_overload=True,
    )
    return Inventory(
        SourceInventory(
            package="numeric",
            root="/numeric",
            modules=(
                ModuleRecord(
                    "numeric",
                    "__init__.pyi",
                    True,
                    imports=(ImportBinding("array", "numeric._native.array"),),
                    exports=("mean", "array", "pi"),
                    source_kind="stub",
                ),
                ModuleRecord(
                    "numeric._native",
                    "_native.cp312-win_amd64.pyd",
                    False,
                    source_kind="native",
                ),
            ),
            classes=(),
            methods=(),
            dynamic_members=(),
            functions=(function,),
            module_members=(
                ModuleMember(
                    "numeric.array:1",
                    "numeric",
                    "array",
                    "import",
                    "numeric._native.array",
                    "",
                    "__init__.pyi",
                    1,
                ),
                ModuleMember(
                    "numeric.pi:2",
                    "numeric",
                    "pi",
                    "constant",
                    "3.141592653589793",
                    "float",
                    "__init__.pyi",
                    2,
                ),
                ModuleMember(
                    "numeric._native.array",
                    "numeric._native",
                    "array",
                    "native",
                    "",
                    "",
                    "_native.cp312-win_amd64.pyd",
                    0,
                ),
            ),
        ),
        (),
        (),
    )


def test_module_exports_preserve_stub_signatures_and_native_bindings(
    tmp_path: Path, module_inventory: Inventory
) -> None:
    output = export.write_inventory(module_inventory, tmp_path / "numeric")

    with (output / "modules.csv").open(encoding="utf-8", newline="") as handle:
        modules = list(csv.DictReader(handle))
    assert [(row["name"], row["source_kind"]) for row in modules] == [
        ("numeric", "stub"),
        ("numeric._native", "native"),
    ]
    assert json.loads(modules[0]["imports"]) == [
        {
            "name": "array",
            "target": "numeric._native.array",
            "kind": "import",
            "expression": "",
        },
    ]
    with (output / "functions.csv").open(encoding="utf-8", newline="") as handle:
        functions = list(csv.DictReader(handle))
    assert len(functions) == 1
    assert functions[0]["signature"] == module_inventory.source.functions[0].signature
    assert json.loads(functions[0]["parameters"])[0]["kind"] == "POSITIONAL_ONLY"
    assert json.loads(functions[0]["parameters"])[1]["default"] == "None"
    assert json.loads(functions[0]["decorators"]) == ["overload"]
    with (output / "module_members.csv").open(encoding="utf-8", newline="") as handle:
        members = list(csv.DictReader(handle))
    assert len(members) == 3
    assert members[2]["kind"] == "native"
    assert members[2]["expression"] == ""
    counts = export.summary(module_inventory)["counts"]
    assert isinstance(counts, dict)
    assert counts["functions"] == 1
    assert counts["function_parameters"] == 2
    assert counts["module_members"] == 3
    assert counts["api_methods"] == 0
    assert (output / "inventory.jsonl").read_bytes() == b""

    connection = sqlite3.connect(output / "inventory.sqlite")
    try:
        assert connection.execute(
            "SELECT function.name, parameter.kind, parameter.default_expression "
            "FROM functions AS function JOIN function_parameters AS parameter "
            "ON parameter.function_id = function.id ORDER BY parameter.position"
        ).fetchall() == [
            ("mean", "POSITIONAL_ONLY", ""),
            ("mean", "KEYWORD_ONLY", "None"),
        ]
        assert connection.execute(
            "SELECT expression FROM function_decorators"
        ).fetchall() == [("overload",)]
        assert connection.execute(
            "SELECT source_kind FROM modules ORDER BY name"
        ).fetchall() == [("stub",), ("native",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_module_function_transport_uses_function_foreign_keys(
    tmp_path: Path, module_inventory: Inventory
) -> None:
    function = module_inventory.source.functions[0]
    evidence = TransportEvidence(
        function.id + ":transport:1",
        function.id,
        "",
        "numeric",
        "__init__.pyi",
        3,
        "request",
        "GET",
        "/generated-path",
        "literal",
    )
    inventory = replace(
        module_inventory,
        source=replace(
            module_inventory.source,
            functions=(replace(function, transport=(evidence,)),),
        ),
    )
    output = export.write_inventory(inventory, tmp_path / "function_transport")

    connection = sqlite3.connect(output / "inventory.sqlite")
    try:
        assert connection.execute(
            "SELECT function.name, transport.operation, transport.route "
            "FROM functions AS function JOIN function_transport AS transport "
            "ON transport.function_id = function.id"
        ).fetchall() == [("mean", "GET", "/generated-path")]
        assert connection.execute(
            "SELECT COUNT(*) FROM transport_evidence"
        ).fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()

    with (output / "functions.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert json.loads(rows[0]["transport"])[0]["route"] == "/generated-path"


def test_support_source_inheritance_preserves_metadata_and_foreign_keys(
    tmp_path: Path,
) -> None:
    primary_class = "application.Client"
    support_class = "foundation.Base"
    method = MethodRecord(
        id="foundation.Base.run:base.py:4",
        class_id=support_class,
        name="run",
        signature="run(self, value: int) -> int",
        return_annotation="int",
        parameters=(
            Parameter("self", "POSITIONAL_OR_KEYWORD"),
            Parameter("value", "POSITIONAL_OR_KEYWORD", "int"),
        ),
        decorators=(),
        docstring="Run an inherited support-package operation.",
        file="base.py",
        line=4,
        end_line=5,
        is_async=False,
        is_property=False,
        is_overload=False,
    )
    reference = SourceReference("foundation", "/support/foundation", "3.2.1")
    source = SourceInventory(
        package="application",
        root="/primary/application",
        modules=(
            ModuleRecord(
                "application",
                "__init__.py",
                True,
                imports=(ImportBinding("Parent", "foundation.Base", "alias"),),
            ),
            ModuleRecord("foundation", "base.py", True),
        ),
        classes=(
            ClassRecord(
                primary_class,
                "application",
                "Client",
                "Client",
                ("Parent",),
                (),
                "",
                "__init__.py",
                2,
            ),
            ClassRecord(
                support_class,
                "foundation",
                "Base",
                "Base",
                (),
                (),
                "",
                "base.py",
                3,
            ),
        ),
        methods=(method,),
        dynamic_members=(),
        support_sources=(reference,),
    )
    inventory = Inventory(
        source,
        (
            ApiMethod(
                "application.client.run",
                primary_class,
                primary_class,
                support_class,
                "client.run",
                "run",
                method.id,
                "sdk_helper",
                True,
            ),
        ),
        (),
    )

    output = export.write_inventory(inventory, tmp_path / "merged")

    expected_sources = [
        {"package": "foundation", "root": "/support/foundation", "version": "3.2.1"},
    ]
    assert export.summary(inventory)["support_sources"] == expected_sources
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["support_sources"] == expected_sources
    assert summary["source_root"] == "/primary/application"
    with (output / "modules.csv").open(encoding="utf-8", newline="") as handle:
        modules = list(csv.DictReader(handle))
    assert json.loads(modules[0]["imports"]) == [
        {
            "name": "Parent",
            "target": "foundation.Base",
            "kind": "alias",
            "expression": "",
        },
    ]
    connection = sqlite3.connect(output / "inventory.sqlite")
    try:
        assert connection.execute(
            "SELECT api.client_path, api.inherited, definition.module, method.file "
            "FROM api_methods AS api JOIN classes AS definition "
            "ON definition.id = api.defined_in JOIN methods AS method "
            "ON method.id = api.method_id"
        ).fetchall() == [("client.run", 1, "foundation", "base.py")]
        assert connection.execute(
            "SELECT name, target, kind FROM module_imports"
        ).fetchall() == [("Parent", "foundation.Base", "alias")]
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        assert json.loads(metadata["support_sources"]) == expected_sources
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("binding", "receiver", "implicit"),
    (("instance", "this", True), ("class", "klass", True), ("static", "self", False)),
)
def test_caller_parameters_follow_implicit_flags_and_preserve_raw_definition(
    tmp_path: Path, inventory: Inventory, binding: str, receiver: str, implicit: bool
) -> None:
    original = inventory.source.methods[0]
    method = replace(
        original,
        binding=binding,
        parameters=(
            Parameter(receiver, "POSITIONAL_ONLY", implicit=implicit),
            *original.parameters,
        ),
    )
    inventory = replace(inventory, source=replace(inventory.source, methods=(method,)))

    output = export.write_inventory(inventory, tmp_path / binding)

    with (output / "api_methods.csv").open(encoding="utf-8", newline="") as handle:
        api = next(csv.DictReader(handle))
    expected_names = ["name", "tags"] if implicit else [receiver, "name", "tags"]
    assert [
        parameter["name"] for parameter in json.loads(api["parameters"])
    ] == expected_names
    assert api["binding"] == binding
    record = json.loads((output / "inventory.jsonl").read_text(encoding="utf-8"))
    assert record["parameters"][0]["name"] == receiver
    assert record["parameters"][0]["required"]
    assert record["parameters"][0]["implicit"] is implicit
    assert [
        parameter["name"] for parameter in record["call_parameters"]
    ] == expected_names
    with (output / "parameters.csv").open(encoding="utf-8", newline="") as handle:
        parameters = list(csv.DictReader(handle))
    assert parameters[0]["name"] == receiver
    assert parameters[0]["implicit"] == str(implicit).lower()
    connection = sqlite3.connect(output / "inventory.sqlite")
    try:
        assert connection.execute("SELECT binding FROM methods").fetchone() == (
            binding,
        )
        assert connection.execute(
            "SELECT name, implicit FROM parameters WHERE position=0"
        ).fetchone() == (receiver, int(implicit))
    finally:
        connection.close()


def test_expected_response_fields_and_declaring_classes_are_preserved(
    tmp_path: Path, inventory: Inventory
) -> None:
    model_id = "synthetic.Response"
    base_id = "synthetic.BaseResponse"
    method = replace(inventory.source.methods[0], return_annotation="Response")
    fields = (
        ClassField(
            base_id + ".id", base_id, "id", "str", "", "required", "__init__.py", 20
        ),
        ClassField(
            model_id + ".values",
            model_id,
            "values",
            "list[str]",
            "[]",
            "optional",
            "__init__.py",
            25,
        ),
        ClassField(
            model_id + ".version",
            model_id,
            "version",
            "ClassVar[int]",
            "1",
            "optional",
            "__init__.py",
            26,
            "class_variable",
        ),
    )
    response = CallableOutput(
        method.id,
        "Response",
        (model_id,),
        (
            OutputField(model_id, base_id, "id", "str", "", "required", True),
            OutputField(
                model_id, model_id, "values", "list[str]", "[]", "optional", False
            ),
        ),
        "resolved",
    )
    inventory = replace(
        inventory,
        source=replace(
            inventory.source,
            methods=(method,),
            classes=(
                *inventory.source.classes,
                ClassRecord(
                    base_id,
                    "synthetic",
                    "BaseResponse",
                    "BaseResponse",
                    (),
                    (),
                    "",
                    "__init__.py",
                    19,
                ),
                ClassRecord(
                    model_id,
                    "synthetic",
                    "Response",
                    "Response",
                    ("BaseResponse",),
                    (),
                    "",
                    "__init__.py",
                    24,
                ),
            ),
            class_fields=fields,
        ),
        callable_outputs=(response,),
    )

    output = export.write_inventory(inventory, tmp_path / "response")

    with (output / "api_methods.csv").open(encoding="utf-8", newline="") as handle:
        api = next(csv.DictReader(handle))
    expected = json.loads(json.dumps(asdict(response)))
    assert json.loads(api["expected_output"]) == expected
    assert expected["fields"][0]["inherited"]
    record = json.loads((output / "inventory.jsonl").read_text(encoding="utf-8"))
    assert record["expected_output"] == expected
    with (output / "class_fields.csv").open(encoding="utf-8", newline="") as handle:
        raw_fields = list(csv.DictReader(handle))
    assert len(raw_fields) == 3
    assert raw_fields[-1]["kind"] == "class_variable"
    counts = export.summary(inventory)["counts"]
    assert isinstance(counts, dict)
    assert counts["class_fields"] == 3
    assert counts["output_records"] == 1
    assert counts["unknown_outputs"] == 0
    connection = sqlite3.connect(output / "inventory.sqlite")
    try:
        assert connection.execute(
            "SELECT name, default_expression, requiredness "
            "FROM class_fields ORDER BY line"
        ).fetchall() == [
            ("id", "", "required"),
            ("values", "[]", "optional"),
            ("version", "1", "optional"),
        ]
        assert connection.execute(
            "SELECT model_id FROM method_output_models"
        ).fetchall() == [(model_id,)]
        stored = connection.execute("SELECT output_json FROM method_outputs").fetchone()
        assert stored is not None
        assert json.loads(stored[0]) == expected
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize("status", ("unknown", "unannotated", "unresolved", "partial"))
def test_output_gaps_remain_explicit_in_function_exports(
    tmp_path: Path, module_inventory: Inventory, status: str
) -> None:
    function = module_inventory.source.functions[0]
    result = CallableOutput(
        function.id,
        "ExternalResponse",
        (),
        (),
        status,
        ("external.Response",),
    )
    inventory = replace(module_inventory, callable_outputs=(result,))

    output = export.write_inventory(inventory, tmp_path / status)

    with (output / "functions.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    data = json.loads(row["expected_output"])
    assert data["status"] == status
    assert data["unresolved"] == ["external.Response"]
    assert data["fields"] == []
    counts = export.summary(inventory)["counts"]
    assert isinstance(counts, dict)
    assert counts["unknown_outputs"] == int(status != "partial")
    assert counts["partial_outputs"] == int(status == "partial")
    connection = sqlite3.connect(output / "inventory.sqlite")
    try:
        assert connection.execute("SELECT status FROM function_outputs").fetchone() == (
            status,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM function_output_models"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_cached_alias_records_match_complete_definition_after_eviction(
    tmp_path: Path, inventory: Inventory
) -> None:
    original = inventory.source.methods[0]
    methods = tuple(
        replace(original, id=f"method:{index}", transport=()) for index in range(1025)
    )
    aliases = tuple(
        replace(
            inventory.api_methods[0],
            id=f"api:{index}",
            method_id=method.id,
            client_path=f"client.alias_{index}.create",
        )
        for index, method in enumerate((*methods, methods[0], methods[-1], methods[0]))
    )
    inventory = replace(
        inventory,
        source=replace(inventory.source, methods=methods),
        api_methods=aliases,
    )

    output = export.write_inventory(inventory, tmp_path / "aliases")

    records = (output / "inventory.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == len(aliases)
    for index in (0, 1024, 1025, 1026, 1027):
        method = methods[0] if index in (0, 1025, 1027) else methods[-1]
        expected = asdict(method)
        expected.update(asdict(aliases[index]))
        expected["call_parameters"] = tuple(
            asdict(parameter)
            for parameter in method.parameters
            if not parameter.implicit
        )
        expected["expected_output"] = {
            "callable_id": method.id,
            "annotation": method.return_annotation,
            "model_ids": (),
            "fields": (),
            "status": "unknown",
            "unresolved": (),
        }
        assert records[index] == json.dumps(
            expected, ensure_ascii=False, sort_keys=True
        )
