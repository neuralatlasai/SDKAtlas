"""Verify caller arguments and source-derived output fields without evaluation."""

import csv
import json
from pathlib import Path

from sdk_atlas.graph import resolve_inventory
from sdk_atlas.scanner import scan_source


def _inventory(tmp_path: Path, code: str, *, budget: int = 100_000):
    source = tmp_path / "sample"
    source.mkdir()
    (source / "__init__.py").write_text(code, encoding="utf-8")
    return resolve_inventory(
        scan_source(source, "sample"), root_classes=("Client",), max_paths=budget
    )


def test_response_fields_include_inherited_overrides_and_exclude_classvars(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing import ClassVar
class Parent:
    identifier: int
    label: str = "default"
class Result(Parent):
    label: str
    metadata: ClassVar[str] = "class-only"
    count: int = 0
class Client:
    def fetch(this, *, name: str) -> Result:
        raise RuntimeError("must never execute")
""",
    )
    result = inventory.callable_outputs[0]
    assert result.status == "resolved"
    assert result.model_ids == ("sample.Result",)
    fields = {field.name: field for field in result.fields}
    assert set(fields) == {"identifier", "label", "count"}
    assert fields["identifier"].inherited
    assert fields["label"].requiredness == "required"
    assert fields["count"].default == "0"
    assert inventory.source.methods[0].parameters[0].implicit


def test_typed_dict_total_and_required_wrappers_preserve_field_requirements(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing import TypedDict, Required, NotRequired
class Result(TypedDict, total=False):
    optional: str
    mandatory: Required[int]
    extra: NotRequired[str]
class Client:
    def fetch(self) -> list[Result]: ...
""",
    )
    fields = {field.name: field for field in inventory.callable_outputs[0].fields}
    assert fields["optional"].requiredness == "optional"
    assert fields["mandatory"].requiredness == "required"
    assert fields["extra"].requiredness == "optional"
    assert inventory.callable_outputs[0].annotation == "list[Result]"


def test_literals_annotation_metadata_and_callable_arguments_are_not_models(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing import Annotated, Literal, Callable
class Result:
    value: int
class Client:
    def label(self) -> Literal["unknown_name"]: ...
    def marked(self) -> Annotated[Result, "description, not a type"]: ...
    def factory(self) -> Callable[[], Result]: ...
""",
    )
    records = {record.annotation: record for record in inventory.callable_outputs}
    assert (
        next(
            record
            for annotation, record in records.items()
            if annotation.startswith("Literal")
        ).status
        == "type_only"
    )
    assert next(
        record
        for annotation, record in records.items()
        if annotation.startswith("Annotated")
    ).model_ids == ("sample.Result",)
    assert (
        next(
            record
            for annotation, record in records.items()
            if annotation.startswith("Callable")
        ).model_ids
        == ()
    )


def test_unannotated_unresolved_any_and_partial_outputs_stay_explicit(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing import Any
from external_library import Model
class Result(Model):
    value: int
class Client:
    def plain(self): ...
    def missing(self) -> Missing: ...
    def dynamic(self) -> Any: ...
    def partial(self) -> Result: ...
    def inferred(self): return Result()
""",
    )
    names = {method.id: method.name for method in inventory.source.methods}
    records = {
        names[record.callable_id]: record for record in inventory.callable_outputs
    }
    assert records["plain"].status == "unannotated"
    assert records["missing"].status == "unresolved"
    assert records["dynamic"].status == "unknown"
    assert records["partial"].status == "partial"
    assert records["inferred"].status == "partial"
    assert records["partial"].unresolved == ("external_library.Model",)


def test_static_class_and_renamed_receivers_distinguish_caller_arguments(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
class Client:
    def instance(this, value: int) -> int: ...
    @classmethod
    def from_value(kind, value: int) -> int: ...
    @staticmethod
    def static(self: int) -> int: ...
""",
    )
    methods = {method.name: method for method in inventory.source.methods}
    assert methods["instance"].binding == "instance"
    assert methods["instance"].parameters[0].implicit
    assert methods["from_value"].binding == "class"
    assert methods["from_value"].parameters[0].implicit
    assert methods["static"].binding == "static"
    assert not methods["static"].parameters[0].implicit


def test_field_expansion_budget_reports_partial_structure(tmp_path: Path) -> None:
    annotations = "\n".join(f"    field_{index}: int" for index in range(20))
    inventory = _inventory(
        tmp_path,
        f"class Result:\n{annotations}\nclass Client:\n"
        "    def fetch(self) -> Result: ...\n",
        budget=5,
    )
    output = inventory.callable_outputs[0]
    assert len(output.fields) == 5
    assert output.status == "partial"
    assert any(issue.kind == "output_field_limit" for issue in inventory.issues)


def test_module_function_output_fields_do_not_require_client_graph(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.py"
    path.write_text(
        "class Result:\n    value: int\ndef calculate() -> Result: ...\n",
        encoding="utf-8",
    )
    inventory = resolve_inventory(scan_source(path, "sample"))
    assert inventory.callable_outputs[0].model_ids == ("sample.Result",)
    assert inventory.callable_outputs[0].fields[0].name == "value"


def test_repeated_response_models_share_immutable_field_projection(
    tmp_path: Path,
) -> None:
    fields = "\n".join(f"    field_{index}: int" for index in range(200))
    methods = "\n".join(
        f"    def fetch_{index}(self) -> Result: ..." for index in range(200)
    )
    inventory = _inventory(
        tmp_path, f"class Result:\n{fields}\nclass Client:\n{methods}\n"
    )
    outputs = inventory.callable_outputs
    assert len(outputs) == 200
    assert len(outputs[0].fields) == 200
    assert all(output.fields is outputs[0].fields for output in outputs)


def test_union_projections_are_cached_and_bounded(tmp_path: Path) -> None:
    inventory = _inventory(
        tmp_path,
        """
class First:
    first: int
class Second:
    second: int
class Third:
    third: int
class Client:
    def one(self) -> First | Second: ...
    def two(self) -> First | Second: ...
    def three(self) -> Second | Third: ...
""",
        budget=3,
    )
    outputs = inventory.callable_outputs
    assert outputs[0].fields is outputs[1].fields
    assert outputs[2].status == "partial"
    assert len(outputs[2].fields) == 1
    assert any(issue.kind == "output_projection_limit" for issue in inventory.issues)


def test_generic_union_and_private_type_aliases_preserve_response_models(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing import Annotated, TypeAlias, Union
class Result:
    value: int
class Other:
    name: str
_Choice = Result | Other
Choice: TypeAlias = Annotated[Union[Result, Other], 'metadata']
Many = list[Result]
class Client:
    def one(self) -> Choice: ...
    def two(self) -> Many: ...
    def three(self) -> _Choice: ...
""",
    )
    outputs = inventory.callable_outputs
    assert outputs[0].model_ids == ("sample.Result", "sample.Other")
    assert outputs[1].model_ids == ("sample.Result",)
    assert outputs[2].model_ids == outputs[0].model_ids
    assert outputs[2].fields is outputs[0].fields
    assert all(output.status == "resolved" for output in outputs)


def test_imported_alias_expansion_preserves_defining_module_scope(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sample"
    source.mkdir()
    (source / "__init__.py").write_text(
        "from .types import Many\nclass Client:\n    def fetch(self) -> Many: ...\n",
        encoding="utf-8",
    )
    (source / "types.py").write_text(
        "class Result:\n    value: int\nMany = list[Result]\n", encoding="utf-8"
    )
    inventory = resolve_inventory(
        scan_source(source, "sample"), root_classes=("Client",)
    )
    assert inventory.callable_outputs[0].model_ids == ("sample.types.Result",)
    assert inventory.callable_outputs[0].fields[0].name == "value"


def test_cyclic_aliases_and_dynamic_annotations_remain_incomplete(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing import Self, TypeGuard
First = list[Second]
Second = list[First]
class Result:
    value: int
class Client:
    def cycle(self) -> First: ...
    def copy(self) -> Self: ...
    def predicate(self) -> TypeGuard[Result]: ...
    def factory(self) -> schema_factory(): ...
    def value(self) -> 123: ...
    def empty(self) -> None: ...
""",
    )
    names = {method.id: method.name for method in inventory.source.methods}
    outputs = {names[item.callable_id]: item for item in inventory.callable_outputs}
    assert outputs["cycle"].status in {"unknown", "unresolved"}
    assert outputs["copy"].status == "unknown"
    assert outputs["predicate"].status == "type_only"
    assert not outputs["predicate"].fields
    assert outputs["factory"].status == "unknown"
    assert outputs["value"].status == "unknown"
    assert outputs["empty"].status == "type_only"


def test_field_qualifiers_require_typing_provenance_and_support_nested_wrappers(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing_extensions import ReadOnly, NotRequired, TypedDict
class ClassVar: ...
class Result(TypedDict):
    data: ClassVar[int]
    optional: ReadOnly[NotRequired[str]]
class Client:
    def fetch(self) -> Result: ...
""",
    )
    fields = {field.name: field for field in inventory.callable_outputs[0].fields}
    assert set(fields) == {"data", "optional"}
    assert fields["optional"].requiredness == "optional"


def test_repeated_field_declarations_on_one_line_have_unique_record_ids(
    tmp_path: Path,
) -> None:
    from sdk_atlas.export import write_inventory

    inventory = _inventory(
        tmp_path,
        "class Result:\n    value: int; value: str\nclass Client:\n"
        "    def fetch(self) -> Result: ...\n",
    )
    assert len({field.id for field in inventory.source.class_fields}) == 2
    assert inventory.callable_outputs[0].fields[0].annotation == "str"
    assert (
        write_inventory(inventory, tmp_path / "output") / "inventory.sqlite"
    ).is_file()


def test_functional_typing_factories_resolve_private_bases_and_response_fields(
    tmp_path: Path,
) -> None:
    inventory = _inventory(
        tmp_path,
        """
from typing import TypedDict as TD, NamedTuple, Required
_Reserved = TD('_Reserved', {'class': Required[str]}, total=False)
Pair = NamedTuple('Pair', [('first', int), ('second', str)])
class Result(_Reserved):
    value: int
class Client:
    def result(self) -> Result: ...
    def pair(self) -> Pair: ...
""",
    )
    outputs = inventory.callable_outputs
    fields = {field.name: field for field in outputs[0].fields}
    assert set(fields) == {"class", "value"}
    assert fields["class"].requiredness == "required"
    assert {field.name for field in outputs[1].fields} == {"first", "second"}
    assert not any(issue.kind == "unresolved_base" for issue in inventory.issues)


def test_all_definitions_include_unreachable_methods_properties_and_protocols(
    tmp_path: Path,
) -> None:
    from sdk_atlas.export import summary, write_inventory

    inventory = _inventory(
        tmp_path,
        '''
class Result:
    value: int
class Independent:
    def __init__(this, *, option: int = 1):
        """Configure the independent class."""
    def __call__(this, value: int, /) -> Result: ...
    @property
    def answer(this) -> Result: ...
    def calculate(this, *, count: int) -> Result:
        """Calculate a typed result."""
    def _private(this): ...
class Client:
    def ping(self) -> str: ...
''',
    )
    assert len(inventory.source.methods) == 5
    assert {method.client_path for method in inventory.api_methods} == {"client.ping"}
    outputs = {item.callable_id: item for item in inventory.callable_outputs}
    assert {method.id for method in inventory.source.methods} == outputs.keys()
    destination = write_inventory(inventory, tmp_path / "export")
    with (destination / "methods.csv").open(encoding="utf-8", newline="") as handle:
        methods = {row["name"]: row for row in csv.DictReader(handle)}
    assert set(methods) == {"__init__", "__call__", "answer", "calculate", "ping"}
    assert json.loads(methods["calculate"]["client_paths"]) == []
    assert methods["calculate"]["docstring"] == "Calculate a typed result."
    assert json.loads(methods["calculate"]["parameters"])[0]["name"] == "count"
    assert json.loads(methods["__init__"]["parameters"])[0]["name"] == "option"
    assert json.loads(methods["__call__"]["parameters"])[0]["kind"] == "POSITIONAL_ONLY"
    assert json.loads(methods["calculate"]["expected_output"])["fields"][0]["name"] == (
        "value"
    )
    counts = summary(inventory)["counts"]
    assert counts["methods_without_api_path"] == 4
    assert counts["methods_with_output_analysis"] == 5


def test_class_callable_aliases_preserve_signatures_docs_outputs_and_transport(
    tmp_path: Path,
) -> None:
    from sdk_atlas.export import write_inventory

    inventory = _inventory(
        tmp_path,
        '''
class Result:
    value: int
class Client:
    def _implementation(this, value: int, /, *, count: int = 2) -> Result:
        """Create a result."""
        return this._post('/result')
    create = _implementation
    alias = create
    __call__ = create
''',
    )
    methods = {method.name: method for method in inventory.source.methods}
    assert set(methods) == {"create", "alias", "__call__"}
    assert methods["create"].signature.startswith("create(")
    assert methods["alias"].docstring == "Create a result."
    assert methods["alias"].parameters[0].implicit
    assert {method.client_path for method in inventory.api_methods} == {
        "client.create",
        "client.alias",
    }
    assert all(
        output.fields[0].name == "value" for output in inventory.callable_outputs
    )
    assert (write_inventory(inventory, tmp_path / "output") / "methods.csv").is_file()


def test_incomplete_local_ancestry_does_not_claim_resolved_response_shape(
    tmp_path: Path,
) -> None:
    from sdk_atlas.outputs import resolve_outputs

    module = tmp_path / "sample.py"
    module.write_text(
        "class Parent:\n    inherited: int\nclass Child(Parent):\n"
        "    local: str\nclass Client:\n    def fetch(self) -> Child: ...\n",
        encoding="utf-8",
    )
    source = scan_source(module, "sample")
    classes = {record.id for record in source.classes}

    def resolve(scope: str, reference: str) -> str:
        candidate = f"{scope}.{reference}"
        return candidate if candidate in classes else ""

    def classify(scope: str, reference: str) -> tuple[str, str]:
        target = resolve(scope, reference)
        return ("local", target) if target else ("builtin", reference)

    outputs, _ = resolve_outputs(
        source,
        resolve_class=resolve,
        ancestors={"sample.Child": ("sample.Child",)},
        callable_ids=frozenset(method.id for method in source.methods),
        max_records=100,
        classify_reference=classify,
    )
    assert outputs[0].status == "partial"
    assert outputs[0].unresolved == ("sample.Parent",)
