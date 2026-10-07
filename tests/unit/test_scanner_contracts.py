"""Cover independently specified declaration and descriptor edge contracts."""

from pathlib import Path

import pytest

from sdk_atlas.graph import resolve_inventory
from sdk_atlas.scanner import scan_source


def test_instance_annotation_wrappers_preserve_all_resource_candidates(
    tmp_path: Path,
) -> None:
    module = tmp_path / "sample.py"
    module.write_text(
        """
from typing import Annotated, Optional, Union
class Left:
    def left(self): ...
class Right:
    def right(self): ...
class Client:
    def __init__(self):
        self.optional: Optional[Left]
        self.union: Union[Left, Right]
        self.infix: Left | Right
        self.quoted: "Left | Right"
        self.annotated: Annotated[Left, "do not treat this metadata as a class"]
        self.invalid: "[unclosed"
""",
        encoding="utf-8",
    )
    source = scan_source(module, "sample")
    assert source.issues == ()
    assert {member.name: member.target_refs for member in source.dynamic_members} == {
        "optional": ("Left",),
        "union": ("Left", "Right"),
        "infix": ("Left", "Right"),
        "quoted": ("Left", "Right"),
        "annotated": ("Left",),
        "invalid": (),
    }
    inventory = resolve_inventory(source, root_classes=("Client",))
    assert {api.client_path for api in inventory.api_methods} == {
        "client.optional.left",
        "client.union.left",
        "client.union.right",
        "client.infix.left",
        "client.infix.right",
        "client.quoted.left",
        "client.quoted.right",
        "client.annotated.left",
    }


@pytest.mark.parametrize(
    "declaration",
    [
        'TypedDict("Dynamic", {1: int})',
        'TypedDict("Dynamic", {**other})',
        'TypedDict("Dynamic", other)',
        'NamedTuple("Dynamic", [("name", int, str)])',
        'NamedTuple("Dynamic", [(1, int)])',
        'NamedTuple("Dynamic", fields)',
    ],
)
def test_nonliteral_typing_factories_do_not_invent_model_fields(
    tmp_path: Path, declaration: str
) -> None:
    module = tmp_path / "sample.py"
    module.write_text(
        f"from typing import NamedTuple, TypedDict\nDynamic = {declaration}\n"
        "def call() -> Dynamic: ...\n",
        encoding="utf-8",
    )
    source = scan_source(module, "sample")
    assert source.issues == ()
    assert source.classes == ()
    assert source.class_fields == ()
    output = resolve_inventory(source).callable_outputs[0]
    assert output.model_ids == ()
    assert output.status != "resolved"


def test_dynamic_total_and_annotation_aliases_preserve_uncertainty(
    tmp_path: Path,
) -> None:
    module = tmp_path / "sample.py"
    module.write_text(
        """
from typing import TypedDict, TypeAlias
Record = TypedDict("Record", {"value": int}, total=runtime_flag)
Alias: TypeAlias = Record | None
async def fetch() -> Alias: ...
class Outer:
    _hidden: int
    class Inner:
        value: int
""",
        encoding="utf-8",
    )
    source = scan_source(module, "sample")
    assert source.issues == ()
    assert {record.id for record in source.classes} == {
        "sample.Record",
        "sample.Outer",
        "sample.Outer.Inner",
    }
    assert {field.name for field in source.class_fields} == {"value"}
    result = resolve_inventory(source)
    output = next(
        record
        for record in result.callable_outputs
        if record.callable_id.startswith("sample.fetch:")
    )
    # Factory fields are known, while the external typing base is still explicit.
    assert output.status == "partial"
    assert output.unresolved == ("typing.TypedDict",)
    assert [(field.name, field.requiredness) for field in output.fields] == [
        ("value", "unknown")
    ]
    assert source.functions[0].is_async


@pytest.mark.parametrize("limit", ["max_files", "max_source_bytes", "max_ast_nodes"])
def test_nonpositive_scan_bounds_fail_before_source_access(
    tmp_path: Path, limit: str
) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        scan_source(tmp_path / "absent", "sample", **{limit: 0})


def test_unsupported_explicit_file_and_bad_package_fail_clearly(tmp_path: Path) -> None:
    path = tmp_path / "data.txt"
    path.write_text("not a module", encoding="utf-8")
    with pytest.raises(ValueError, match="Python module"):
        scan_source(path, "sample")
    with pytest.raises(ValueError, match="absolute dotted"):
        scan_source(path, "../sample")
