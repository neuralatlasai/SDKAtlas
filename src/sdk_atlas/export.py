"""Export source evidence and resolved API paths without importing SDK code."""

import csv
import json
import os
import shutil
import sqlite3
import tempfile
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path

from sdk_atlas.models import (
    CallableOutput,
    FunctionRecord,
    Inventory,
    MethodRecord,
    ScanIssue,
)

OUTPUT_FILES = (
    "api_methods.csv",
    "parameters.csv",
    "resource_graph.csv",
    "classes.csv",
    "transport_evidence.csv",
    "dynamic_members.csv",
    "inventory.jsonl",
    "inventory.sqlite",
    "summary.json",
    "modules.csv",
    "functions.csv",
    "module_members.csv",
    "class_fields.csv",
    "methods.csv",
)

_MODULE_COLUMNS = ("name", "file", "is_package", "source_kind")
_FUNCTION_COLUMNS = (
    "id",
    "module",
    "name",
    "signature",
    "return_annotation",
    "docstring",
    "file",
    "line",
    "end_line",
    "is_async",
    "is_overload",
)
_MODULE_MEMBER_COLUMNS = (
    "id",
    "module",
    "name",
    "kind",
    "expression",
    "annotation",
    "file",
    "line",
)
_API_COLUMNS = (
    "id",
    "root_class",
    "resource_class",
    "defined_in",
    "client_path",
    "method_name",
    "method_id",
    "method_kind",
    "inherited",
)
_METHOD_COLUMNS = (
    "id",
    "class_id",
    "name",
    "signature",
    "return_annotation",
    "docstring",
    "file",
    "line",
    "end_line",
    "is_async",
    "is_property",
    "is_overload",
    "binding",
)
_API_DETAIL_COLUMNS = (
    "signature",
    "return_annotation",
    "is_async",
    "is_overload",
    "file",
    "line",
    "docstring",
    "binding",
)
_CLASS_FIELD_COLUMNS = (
    "id",
    "class_id",
    "name",
    "annotation",
    "default",
    "requiredness",
    "file",
    "line",
    "kind",
)
_CLASS_COLUMNS = (
    "id",
    "module",
    "name",
    "qualname",
    "docstring",
    "file",
    "line",
)
_GRAPH_COLUMNS = (
    "id",
    "root_class",
    "source_class",
    "target_class",
    "source_path",
    "target_path",
    "member",
    "kind",
    "file",
    "line",
)
_TRANSPORT_COLUMNS = (
    "id",
    "method_id",
    "class_id",
    "module",
    "file",
    "line",
    "call",
    "operation",
    "route",
    "route_kind",
)
_DYNAMIC_COLUMNS = (
    "id",
    "class_id",
    "name",
    "expression",
    "annotation",
    "file",
    "line",
)
_PARAMETER_COLUMNS = (
    "name",
    "kind",
    "annotation",
    "default",
    "required",
    "implicit",
)

_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE modules (
    name TEXT PRIMARY KEY, file TEXT NOT NULL, is_package INTEGER NOT NULL,
    source_kind TEXT NOT NULL
);
CREATE TABLE module_imports (
    module_name TEXT NOT NULL REFERENCES modules(name), position INTEGER NOT NULL,
    name TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL,
    expression TEXT NOT NULL,
    PRIMARY KEY (module_name, position)
);
CREATE TABLE module_exports (
    module_name TEXT NOT NULL REFERENCES modules(name), position INTEGER NOT NULL,
    name TEXT NOT NULL, PRIMARY KEY (module_name, position)
);
CREATE TABLE functions (
    id TEXT PRIMARY KEY, module TEXT NOT NULL REFERENCES modules(name),
    name TEXT NOT NULL, signature TEXT NOT NULL, return_annotation TEXT NOT NULL,
    docstring TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, is_async INTEGER NOT NULL, is_overload INTEGER NOT NULL
);
CREATE TABLE function_parameters (
    function_id TEXT NOT NULL REFERENCES functions(id), position INTEGER NOT NULL,
    name TEXT NOT NULL, kind TEXT NOT NULL, annotation TEXT NOT NULL,
    default_expression TEXT NOT NULL, required INTEGER NOT NULL,
    implicit INTEGER NOT NULL,
    PRIMARY KEY (function_id, position)
);
CREATE TABLE function_decorators (
    function_id TEXT NOT NULL REFERENCES functions(id), position INTEGER NOT NULL,
    expression TEXT NOT NULL, PRIMARY KEY (function_id, position)
);
CREATE TABLE function_transport (
    id TEXT PRIMARY KEY, function_id TEXT NOT NULL REFERENCES functions(id),
    module TEXT NOT NULL REFERENCES modules(name), file TEXT NOT NULL,
    line INTEGER NOT NULL, call TEXT NOT NULL, operation TEXT NOT NULL,
    route TEXT NOT NULL, route_kind TEXT NOT NULL
);
CREATE TABLE module_members (
    id TEXT PRIMARY KEY, module TEXT NOT NULL REFERENCES modules(name),
    name TEXT NOT NULL, kind TEXT NOT NULL, expression TEXT NOT NULL,
    annotation TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL
);
CREATE TABLE classes (
    id TEXT PRIMARY KEY, module TEXT NOT NULL REFERENCES modules(name),
    name TEXT NOT NULL, qualname TEXT NOT NULL, docstring TEXT NOT NULL,
    file TEXT NOT NULL, line INTEGER NOT NULL
);
CREATE TABLE class_bases (
    class_id TEXT NOT NULL REFERENCES classes(id), position INTEGER NOT NULL,
    expression TEXT NOT NULL, PRIMARY KEY (class_id, position)
);
CREATE TABLE class_fields (
    id TEXT PRIMARY KEY, class_id TEXT NOT NULL REFERENCES classes(id),
    name TEXT NOT NULL, annotation TEXT NOT NULL, default_expression TEXT NOT NULL,
    requiredness TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL,
    kind TEXT NOT NULL
);
CREATE TABLE class_decorators (
    class_id TEXT NOT NULL REFERENCES classes(id), position INTEGER NOT NULL,
    expression TEXT NOT NULL, PRIMARY KEY (class_id, position)
);
CREATE TABLE methods (
    id TEXT PRIMARY KEY, class_id TEXT NOT NULL REFERENCES classes(id),
    name TEXT NOT NULL, signature TEXT NOT NULL, return_annotation TEXT NOT NULL,
    docstring TEXT NOT NULL, file TEXT NOT NULL, line INTEGER NOT NULL,
    end_line INTEGER NOT NULL, is_async INTEGER NOT NULL,
    is_property INTEGER NOT NULL, is_overload INTEGER NOT NULL, binding TEXT NOT NULL
);
CREATE TABLE method_decorators (
    method_id TEXT NOT NULL REFERENCES methods(id), position INTEGER NOT NULL,
    expression TEXT NOT NULL, PRIMARY KEY (method_id, position)
);
CREATE TABLE method_returns (
    method_id TEXT NOT NULL REFERENCES methods(id), position INTEGER NOT NULL,
    expression TEXT NOT NULL, PRIMARY KEY (method_id, position)
);
CREATE TABLE method_delegates (
    method_id TEXT NOT NULL REFERENCES methods(id), position INTEGER NOT NULL,
    expression TEXT NOT NULL, PRIMARY KEY (method_id, position)
);
CREATE TABLE parameters (
    method_id TEXT NOT NULL REFERENCES methods(id), position INTEGER NOT NULL,
    name TEXT NOT NULL, kind TEXT NOT NULL, annotation TEXT NOT NULL,
    default_expression TEXT NOT NULL, required INTEGER NOT NULL,
    implicit INTEGER NOT NULL,
    PRIMARY KEY (method_id, position)
);
CREATE TABLE transport_evidence (
    id TEXT PRIMARY KEY, method_id TEXT NOT NULL REFERENCES methods(id),
    class_id TEXT NOT NULL REFERENCES classes(id),
    module TEXT NOT NULL REFERENCES modules(name), file TEXT NOT NULL,
    line INTEGER NOT NULL, call TEXT NOT NULL, operation TEXT NOT NULL,
    route TEXT NOT NULL, route_kind TEXT NOT NULL
);
CREATE TABLE dynamic_members (
    id TEXT PRIMARY KEY, class_id TEXT NOT NULL REFERENCES classes(id),
    name TEXT NOT NULL, expression TEXT NOT NULL, annotation TEXT NOT NULL,
    file TEXT NOT NULL, line INTEGER NOT NULL
);
CREATE TABLE dynamic_member_targets (
    member_id TEXT NOT NULL REFERENCES dynamic_members(id),
    position INTEGER NOT NULL, expression TEXT NOT NULL,
    PRIMARY KEY (member_id, position)
);
CREATE TABLE api_methods (
    id TEXT PRIMARY KEY, root_class TEXT NOT NULL REFERENCES classes(id),
    resource_class TEXT NOT NULL REFERENCES classes(id),
    defined_in TEXT NOT NULL REFERENCES classes(id), client_path TEXT NOT NULL,
    method_name TEXT NOT NULL, method_id TEXT NOT NULL REFERENCES methods(id),
    method_kind TEXT NOT NULL, inherited INTEGER NOT NULL
);
CREATE TABLE resource_graph (
    id TEXT PRIMARY KEY, root_class TEXT NOT NULL REFERENCES classes(id),
    source_class TEXT NOT NULL REFERENCES classes(id),
    target_class TEXT NOT NULL REFERENCES classes(id), source_path TEXT NOT NULL,
    target_path TEXT NOT NULL, member TEXT NOT NULL, kind TEXT NOT NULL,
    file TEXT NOT NULL, line INTEGER NOT NULL
);
CREATE TABLE issues (
    position INTEGER PRIMARY KEY, kind TEXT NOT NULL, message TEXT NOT NULL,
    file TEXT NOT NULL, line INTEGER NOT NULL
);
CREATE TABLE method_outputs (
    method_id TEXT PRIMARY KEY REFERENCES methods(id), annotation TEXT NOT NULL,
    status TEXT NOT NULL, output_json TEXT NOT NULL
);
CREATE TABLE function_outputs (
    function_id TEXT PRIMARY KEY REFERENCES functions(id), annotation TEXT NOT NULL,
    status TEXT NOT NULL, output_json TEXT NOT NULL
);
CREATE TABLE method_output_models (
    method_id TEXT NOT NULL REFERENCES method_outputs(method_id),
    position INTEGER NOT NULL, model_id TEXT NOT NULL REFERENCES classes(id),
    PRIMARY KEY (method_id, position)
);
CREATE TABLE function_output_models (
    function_id TEXT NOT NULL REFERENCES function_outputs(function_id),
    position INTEGER NOT NULL, model_id TEXT NOT NULL REFERENCES classes(id),
    PRIMARY KEY (function_id, position)
);
CREATE INDEX methods_class ON methods(class_id);
CREATE INDEX class_fields_class ON class_fields(class_id);
CREATE INDEX functions_module ON functions(module);
CREATE INDEX function_transport_definition ON function_transport(function_id);
CREATE INDEX module_members_module ON module_members(module);
CREATE INDEX api_methods_definition ON api_methods(method_id);
CREATE INDEX transport_definition ON transport_evidence(method_id);
CREATE INDEX graph_source ON resource_graph(source_class);
"""


class ExportRecoveryError(OSError):
    """Report failed rollback while retaining original files for recovery."""


def _issues(inventory: Inventory) -> tuple[ScanIssue, ...]:
    # Insertion order preserves deterministic scanner order; the set avoids an
    # O(n²) diagnostic deduplication when graph issues include source issues.
    return tuple(dict.fromkeys((*inventory.source.issues, *inventory.issues)))


def summary(inventory: Inventory) -> dict[str, object]:
    """Return JSON-compatible metadata and counts without filesystem access.

    Args:
        inventory: Immutable source evidence and resolved API paths.

    Returns:
        Package metadata, source/graph counts, and deduplicated diagnostics.
        Counts distinguish all declared definitions from resolved API paths.
    """
    source = inventory.source
    issues = _issues(inventory)
    reached = {method.method_id for method in inventory.api_methods}
    analyzed = {output.callable_id for output in inventory.callable_outputs}
    return {
        "schema_version": 1,
        "package": source.package,
        "version": source.version,
        "source_root": source.root,
        "support_sources": [asdict(reference) for reference in source.support_sources],
        "counts": {
            "modules": len(source.modules),
            "functions": len(source.functions),
            "function_parameters": sum(
                len(function.parameters) for function in source.functions
            ),
            "function_transport": sum(
                len(function.transport) for function in source.functions
            ),
            "module_members": len(source.module_members),
            "classes": len(source.classes),
            "class_fields": len(source.class_fields),
            "output_records": len(inventory.callable_outputs),
            "unknown_outputs": sum(
                output.status in {"unknown", "unannotated", "unresolved"}
                for output in inventory.callable_outputs
            ),
            "partial_outputs": sum(
                output.status == "partial" for output in inventory.callable_outputs
            ),
            "method_definitions": len(source.methods),
            "methods_with_api_path": len(reached),
            "methods_without_api_path": sum(
                method.id not in reached for method in source.methods
            ),
            "methods_with_output_analysis": sum(
                method.id in analyzed for method in source.methods
            ),
            "constructor_protocol_definitions": sum(
                method.name.startswith("__") and method.name.endswith("__")
                for method in source.methods
            ),
            "api_methods": len(inventory.api_methods),
            "parameters": sum(len(method.parameters) for method in source.methods),
            "resource_graph": len(inventory.resource_graph),
            "transport_evidence": sum(
                len(method.transport) for method in source.methods
            ),
            "dynamic_members": len(source.dynamic_members),
            "issues": len(issues),
        },
        "issues": [asdict(issue) for issue in issues],
    }


def _values(record: object, columns: Sequence[str]) -> tuple[object, ...]:
    # Column names are internal constants, never untrusted attribute expressions.
    return tuple(getattr(record, column) for column in columns)


def _output_data(
    callable_id: str,
    annotation: str,
    outputs: dict[str, CallableOutput],
) -> dict[str, object]:
    if callable_id in outputs:
        return asdict(outputs[callable_id])
    # Manual/older inventories have no inference record. Retain the declared
    # annotation and make the absence of analyzed response evidence explicit.
    return {
        "callable_id": callable_id,
        "annotation": annotation,
        "model_ids": (),
        "fields": (),
        "status": "unknown",
        "unresolved": (),
    }


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class _MethodExport:
    record: dict[str, object]
    parameters_json: str
    output_json: str


class _MethodCache:
    """Bound repeated immutable-definition conversion across public aliases."""

    def __init__(
        self,
        methods: dict[str, MethodRecord],
        outputs: dict[str, CallableOutput],
        capacity: int = 1024,
    ) -> None:
        if capacity < 1:
            raise ValueError("serialization cache capacity must be positive")
        self.methods = methods
        self.outputs = outputs
        self.capacity = capacity
        self.records: OrderedDict[str, _MethodExport] = OrderedDict()

    def get(self, method_id: str) -> _MethodExport:
        if method_id in self.records:
            self.records.move_to_end(method_id)
            return self.records[method_id]
        method = self.methods[method_id]
        parameters = tuple(asdict(parameter) for parameter in method.parameters)
        call_parameters = tuple(
            record
            for parameter, record in zip(method.parameters, parameters, strict=True)
            if not parameter.implicit
        )
        output = _output_data(method_id, method.return_annotation, self.outputs)
        record = dict(
            zip(_METHOD_COLUMNS, _values(method, _METHOD_COLUMNS), strict=True)
        )
        record.update(
            {
                "parameters": parameters,
                "decorators": method.decorators,
                "returns": method.returns,
                "delegates": method.delegates,
                "transport": tuple(asdict(evidence) for evidence in method.transport),
                "call_parameters": call_parameters,
                "expected_output": output,
            }
        )
        serialized = _MethodExport(
            record, _json_text(call_parameters), _json_text(output)
        )
        if len(self.records) == self.capacity:
            self.records.popitem(last=False)
        self.records[method_id] = serialized
        return serialized


def _csv_value(value: object) -> object:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_csv(
    path: Path,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(tuple(_csv_value(value) for value in row) for row in rows)


def _api_rows(
    inventory: Inventory, cache: _MethodCache
) -> Iterator[tuple[object, ...]]:
    for api_method in inventory.api_methods:
        serialized = cache.get(api_method.method_id)
        yield (
            *_values(api_method, _API_COLUMNS),
            *(serialized.record[column] for column in _API_DETAIL_COLUMNS),
            serialized.parameters_json,
            serialized.output_json,
        )


def _definition_rows(
    inventory: Inventory, cache: _MethodCache
) -> Iterator[tuple[object, ...]]:
    # Index path associations once; declarations without a path remain visible.
    paths: dict[str, list[tuple[str, str]]] = {}
    for api_method in inventory.api_methods:
        paths.setdefault(api_method.method_id, []).append(
            (api_method.root_class, api_method.client_path)
        )
    for method in inventory.source.methods:
        serialized = cache.get(method.id)
        yield (
            *_values(method, _METHOD_COLUMNS),
            serialized.parameters_json,
            serialized.record["parameters"],
            method.decorators,
            serialized.record["transport"],
            serialized.output_json,
            tuple(paths.get(method.id, ())),
        )


def _parameter_rows(inventory: Inventory) -> Iterator[tuple[object, ...]]:
    for method in inventory.source.methods:
        for position, parameter in enumerate(method.parameters):
            yield (
                method.id,
                method.class_id,
                method.name,
                position,
                *_values(parameter, _PARAMETER_COLUMNS),
            )


def _insert(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    rows: Iterable[Sequence[object]],
) -> None:
    # SQL identifiers come exclusively from this module. All evidence values use
    # bound parameters, including route text and source annotation expressions.
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO {table} ({names}) VALUES ({placeholders})", rows
    )


def _output_rows(
    callables: Sequence[MethodRecord | FunctionRecord],
    outputs: dict[str, CallableOutput],
) -> Iterator[tuple[object, ...]]:
    for record in callables:
        data = _output_data(record.id, record.return_annotation, outputs)
        yield record.id, data["annotation"], data["status"], _json_text(data)


def _write_outputs(connection: sqlite3.Connection, inventory: Inventory) -> None:
    outputs = {output.callable_id: output for output in inventory.callable_outputs}
    groups: tuple[tuple[str, str, Sequence[MethodRecord | FunctionRecord]], ...] = (
        ("method", "method_id", inventory.source.methods),
        ("function", "function_id", inventory.source.functions),
    )
    for prefix, identifier, callables in groups:
        _insert(
            connection,
            f"{prefix}_outputs",
            (identifier, "annotation", "status", "output_json"),
            _output_rows(callables, outputs),
        )
        _insert(
            connection,
            f"{prefix}_output_models",
            (identifier, "position", "model_id"),
            (
                (record.id, position, model_id)
                for record in callables
                if record.id in outputs
                for position, model_id in enumerate(outputs[record.id].model_ids)
            ),
        )


def _write_sqlite(inventory: Inventory, path: Path) -> None:
    source = inventory.source
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA)
        with connection:
            _insert(
                connection,
                "metadata",
                ("key", "value"),
                (
                    ("schema_version", "1"),
                    ("package", source.package),
                    ("version", source.version),
                    ("source_root", source.root),
                    (
                        "support_sources",
                        json.dumps(
                            [asdict(reference) for reference in source.support_sources],
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    ),
                ),
            )
            _insert(
                connection,
                "modules",
                _MODULE_COLUMNS,
                (_values(module, _MODULE_COLUMNS) for module in source.modules),
            )
            _insert(
                connection,
                "module_imports",
                ("module_name", "position", "name", "target", "kind", "expression"),
                (
                    (
                        module.name,
                        position,
                        binding.name,
                        binding.target,
                        binding.kind,
                        binding.expression,
                    )
                    for module in source.modules
                    for position, binding in enumerate(module.imports)
                ),
            )
            _insert(
                connection,
                "module_exports",
                ("module_name", "position", "name"),
                (
                    (module.name, position, name)
                    for module in source.modules
                    for position, name in enumerate(module.exports)
                ),
            )
            _insert(
                connection,
                "functions",
                _FUNCTION_COLUMNS,
                (_values(function, _FUNCTION_COLUMNS) for function in source.functions),
            )
            _insert(
                connection,
                "function_parameters",
                (
                    "function_id",
                    "position",
                    "name",
                    "kind",
                    "annotation",
                    "default_expression",
                    "required",
                    "implicit",
                ),
                (
                    (function.id, position, *_values(parameter, _PARAMETER_COLUMNS))
                    for function in source.functions
                    for position, parameter in enumerate(function.parameters)
                ),
            )
            _insert(
                connection,
                "function_decorators",
                ("function_id", "position", "expression"),
                (
                    (function.id, position, expression)
                    for function in source.functions
                    for position, expression in enumerate(function.decorators)
                ),
            )
            _insert(
                connection,
                "function_transport",
                ("id", "function_id", *_TRANSPORT_COLUMNS[3:]),
                (
                    (
                        evidence.id,
                        evidence.method_id,
                        *_values(evidence, _TRANSPORT_COLUMNS[3:]),
                    )
                    for function in source.functions
                    for evidence in function.transport
                ),
            )
            _insert(
                connection,
                "module_members",
                _MODULE_MEMBER_COLUMNS,
                (
                    _values(member, _MODULE_MEMBER_COLUMNS)
                    for member in source.module_members
                ),
            )
            _insert(
                connection,
                "classes",
                _CLASS_COLUMNS,
                (_values(record, _CLASS_COLUMNS) for record in source.classes),
            )
            _insert(
                connection,
                "class_fields",
                tuple(
                    "default_expression" if column == "default" else column
                    for column in _CLASS_FIELD_COLUMNS
                ),
                (_values(field, _CLASS_FIELD_COLUMNS) for field in source.class_fields),
            )
            for table, attribute in (
                ("class_bases", "bases"),
                ("class_decorators", "decorators"),
            ):
                _insert(
                    connection,
                    table,
                    ("class_id", "position", "expression"),
                    (
                        (record.id, position, expression)
                        for record in source.classes
                        for position, expression in enumerate(
                            getattr(record, attribute)
                        )
                    ),
                )
            _insert(
                connection,
                "methods",
                _METHOD_COLUMNS,
                (_values(record, _METHOD_COLUMNS) for record in source.methods),
            )
            for table, attribute in (
                ("method_decorators", "decorators"),
                ("method_returns", "returns"),
                ("method_delegates", "delegates"),
            ):
                _insert(
                    connection,
                    table,
                    ("method_id", "position", "expression"),
                    (
                        (method.id, position, expression)
                        for method in source.methods
                        for position, expression in enumerate(
                            getattr(method, attribute)
                        )
                    ),
                )
            _insert(
                connection,
                "parameters",
                (
                    "method_id",
                    "position",
                    "name",
                    "kind",
                    "annotation",
                    "default_expression",
                    "required",
                    "implicit",
                ),
                ((row[0], row[3], *row[4:]) for row in _parameter_rows(inventory)),
            )
            _insert(
                connection,
                "transport_evidence",
                _TRANSPORT_COLUMNS,
                (
                    _values(evidence, _TRANSPORT_COLUMNS)
                    for method in source.methods
                    for evidence in method.transport
                ),
            )
            _insert(
                connection,
                "dynamic_members",
                _DYNAMIC_COLUMNS,
                (
                    _values(member, _DYNAMIC_COLUMNS)
                    for member in source.dynamic_members
                ),
            )
            _insert(
                connection,
                "dynamic_member_targets",
                ("member_id", "position", "expression"),
                (
                    (member.id, position, expression)
                    for member in source.dynamic_members
                    for position, expression in enumerate(member.target_refs)
                ),
            )
            _insert(
                connection,
                "api_methods",
                _API_COLUMNS,
                (_values(method, _API_COLUMNS) for method in inventory.api_methods),
            )
            _insert(
                connection,
                "resource_graph",
                _GRAPH_COLUMNS,
                (_values(edge, _GRAPH_COLUMNS) for edge in inventory.resource_graph),
            )
            _insert(
                connection,
                "issues",
                ("position", "kind", "message", "file", "line"),
                (
                    (position, issue.kind, issue.message, issue.file, issue.line)
                    for position, issue in enumerate(_issues(inventory))
                ),
            )
            _write_outputs(connection, inventory)


def _write_artifacts(inventory: Inventory, directory: Path) -> None:
    source = inventory.source
    # A single O(n) index avoids repeatedly searching definitions for each path.
    # Input tuples already carry deterministic scanner/graph order; re-sorting
    # would add O(n log n) work and would erase meaningful declaration order.
    methods = {method.id: method for method in source.methods}
    outputs = {output.callable_id: output for output in inventory.callable_outputs}
    cache = _MethodCache(methods, outputs)
    _write_csv(
        directory / "methods.csv",
        (
            *_METHOD_COLUMNS,
            "parameters",
            "declared_parameters",
            "decorators",
            "transport",
            "expected_output",
            "client_paths",
        ),
        _definition_rows(inventory, cache),
    )
    _write_csv(
        directory / "modules.csv",
        (*_MODULE_COLUMNS, "imports", "exports"),
        (
            (
                *_values(module, _MODULE_COLUMNS),
                tuple(asdict(binding) for binding in module.imports),
                module.exports,
            )
            for module in source.modules
        ),
    )
    _write_csv(
        directory / "functions.csv",
        (
            *_FUNCTION_COLUMNS,
            "parameters",
            "decorators",
            "transport",
            "expected_output",
        ),
        (
            (
                *_values(function, _FUNCTION_COLUMNS),
                tuple(asdict(parameter) for parameter in function.parameters),
                function.decorators,
                tuple(asdict(evidence) for evidence in function.transport),
                _json_text(
                    _output_data(function.id, function.return_annotation, outputs)
                ),
            )
            for function in source.functions
        ),
    )
    _write_csv(
        directory / "module_members.csv",
        _MODULE_MEMBER_COLUMNS,
        (_values(member, _MODULE_MEMBER_COLUMNS) for member in source.module_members),
    )
    _write_csv(
        directory / "api_methods.csv",
        (*_API_COLUMNS, *_API_DETAIL_COLUMNS, "parameters", "expected_output"),
        _api_rows(inventory, cache),
    )
    _write_csv(
        directory / "parameters.csv",
        ("method_id", "class_id", "method_name", "position", *_PARAMETER_COLUMNS),
        _parameter_rows(inventory),
    )
    _write_csv(
        directory / "resource_graph.csv",
        _GRAPH_COLUMNS,
        (_values(edge, _GRAPH_COLUMNS) for edge in inventory.resource_graph),
    )
    _write_csv(
        directory / "classes.csv",
        (*_CLASS_COLUMNS, "bases", "decorators"),
        (
            (*_values(record, _CLASS_COLUMNS), record.bases, record.decorators)
            for record in source.classes
        ),
    )
    _write_csv(
        directory / "class_fields.csv",
        _CLASS_FIELD_COLUMNS,
        (_values(field, _CLASS_FIELD_COLUMNS) for field in source.class_fields),
    )
    _write_csv(
        directory / "transport_evidence.csv",
        _TRANSPORT_COLUMNS,
        (
            _values(evidence, _TRANSPORT_COLUMNS)
            for method in source.methods
            for evidence in method.transport
        ),
    )
    _write_csv(
        directory / "dynamic_members.csv",
        (*_DYNAMIC_COLUMNS, "target_refs"),
        (
            (*_values(member, _DYNAMIC_COLUMNS), member.target_refs)
            for member in source.dynamic_members
        ),
    )
    with (directory / "inventory.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for api_method in inventory.api_methods:
            # Nested definition dictionaries are immutable for this export.
            # Only the shallow copy receives path-specific fields. The bounded
            # cache avoids repeating parameter/output conversions for aliases.
            record = dict(cache.get(api_method.method_id).record)
            record.update(
                zip(_API_COLUMNS, _values(api_method, _API_COLUMNS), strict=True)
            )
            handle.write(_json_text(record) + "\n")
    _write_sqlite(inventory, directory / "inventory.sqlite")
    with (directory / "summary.json").open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        json.dump(
            summary(inventory), handle, ensure_ascii=False, indent=2, sort_keys=True
        )
        handle.write("\n")


def _validate_targets(output: Path) -> None:
    for filename in OUTPUT_FILES:
        target = output / filename
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise FileExistsError(f"Export target must be a regular file: {target}")


def _publish_existing(output: Path, artifacts: Path, backups: Path) -> None:
    published: list[str] = []
    moved: list[str] = []
    try:
        for filename in OUTPUT_FILES:
            target = output / filename
            if target.exists():
                os.replace(target, backups / filename)
                moved.append(filename)
            os.replace(artifacts / filename, target)
            published.append(filename)
    except BaseException:
        # This filesystem transaction boundary restores state even when a user
        # interrupts publication. The original exception is re-raised unchanged.
        rollback_errors: list[OSError] = []
        for filename in reversed(moved):
            try:
                os.replace(backups / filename, output / filename)
            except OSError as error:
                rollback_errors.append(error)
        moved_set = set(moved)
        for filename in reversed(published):
            if filename in moved_set:
                continue
            try:
                (output / filename).unlink()
            except OSError as error:
                rollback_errors.append(error)
        if rollback_errors:
            raise ExportRecoveryError(
                f"Could not restore export files; originals retained in {backups}. "
                f"First rollback failure: {rollback_errors[0]}"
            ) from rollback_errors[0]
        raise


def write_inventory(inventory: Inventory, output: Path) -> Path:
    """Publish all fourteen artifacts, preserving previous exports on failure.

    Args:
        inventory: Immutable records in deterministic source/graph order.
        output: Directory to create or update. Unrelated files are preserved.

    Returns:
        The resolved absolute output directory after successful publication.

    Raises:
        OSError: Output is invalid, artifact generation fails, or publication
            fails. Failed restoration raises ExportRecoveryError and retains
            staged backups at the path in its message.
        sqlite3.Error: Records violate the normalized database constraints.
        KeyError: A resolved API method references a missing definition.

    The export requires exclusive write ownership of its fourteen filenames during
    publication. New directories publish with one rename. Existing directories
    use compensating rollback; concurrent readers may observe replacement in
    progress. No SDK code is executed. Record traversal and additional indexing
    memory are O(n), excluding serialized output size and SQLite's B-tree index
    maintenance. CSV and JSONL streams do not materialize the full export.
    """
    destination = Path(output).resolve()
    if destination.exists() and not destination.is_dir():
        raise NotADirectoryError(f"Export output is not a directory: {destination}")
    _validate_targets(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sdk-atlas-", dir=destination.parent))
    retain_staging = False
    try:
        artifacts = staging / "artifacts"
        backups = staging / "backups"
        artifacts.mkdir()
        backups.mkdir()
        _write_artifacts(inventory, artifacts)
        if destination.exists():
            _validate_targets(destination)
            _publish_existing(destination, artifacts, backups)
        else:
            os.replace(artifacts, destination)
    except ExportRecoveryError:
        retain_staging = True
        raise
    finally:
        if not retain_staging:
            shutil.rmtree(staging)
    return destination
