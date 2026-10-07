"""Generate source variations and verify externally visible semantic invariants."""

from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given
from hypothesis import strategies as st

from sdk_atlas.graph import resolve_inventory
from sdk_atlas.models import Inventory
from sdk_atlas.scanner import scan_source


def _inventory(code: str, **bounds: int) -> Inventory:
    """Keep every example isolated without sharing a pytest fixture lifecycle."""
    with TemporaryDirectory(prefix="sdk-atlas-property-") as directory:
        path = Path(directory) / "sample.py"
        path.write_text(code, encoding="utf-8")
        return resolve_inventory(
            scan_source(path, "sample"), root_classes=("Client",), **bounds
        )


def _resource_source(width: int, depth: int) -> str:
    """Describe a DAG whose reachable leaf paths have an independent product oracle."""
    definitions = ["class Payload:\n    value: int\n"]
    for level in range(depth + 1):
        name = "Client" if level == 0 else f"Resource{level}"
        definitions.append(f"class {name}:\n")
        if level == depth:
            definitions.append("    def fetch(self) -> Payload: ...\n")
            continue
        for index in range(width):
            definitions.append(
                "    @property\n"
                f"    def branch_{index}(self) -> Resource{level + 1}: ...\n"
            )
    return "\n".join(definitions)


@given(chain_length=st.integers(min_value=0, max_value=20))
def test_alias_indirection_preserves_inheritance_and_return_fields(
    chain_length: int,
) -> None:
    """Adding aliases must preserve the public method and its response model."""
    aliases = ["BaseAlias0 = Base", "ResultAlias0 = Payload"]
    for index in range(1, chain_length + 1):
        aliases.extend(
            (
                f"BaseAlias{index} = BaseAlias{index - 1}",
                f"ResultAlias{index} = ResultAlias{index - 1}",
            )
        )
    code = (
        "class Payload:\n    value: int\n"
        f"class Base:\n    def fetch(self) -> ResultAlias{chain_length}: ...\n"
        + "\n".join(aliases)
        + f"\nclass Client(BaseAlias{chain_length}): pass\n"
    )
    inventory = _inventory(code)
    assert not inventory.issues
    assert [method.client_path for method in inventory.api_methods] == ["client.fetch"]
    assert inventory.api_methods[0].defined_in == "sample.Base"
    assert inventory.api_methods[0].inherited
    output = next(
        output
        for output in inventory.callable_outputs
        if output.callable_id == inventory.api_methods[0].method_id
    )
    assert output.status == "resolved"
    assert output.model_ids == ("sample.Payload",)
    assert [(field.name, field.annotation) for field in output.fields] == [
        ("value", "int")
    ]


@given(length=st.integers(min_value=1, max_value=20))
def test_cyclic_aliases_preserve_independent_methods_without_fabricated_edges(
    length: int,
) -> None:
    aliases = "\n".join(
        f"Alias{index} = Alias{(index + 1) % length}" for index in range(length)
    )
    inventory = _inventory(
        aliases + "\nclass Client(Alias0):\n"
        "    def ping(self) -> int: ...\n"
        "    @property\n"
        "    def resource(self) -> Alias0: ...\n"
    )
    assert any(issue.kind == "unresolved_base" for issue in inventory.issues)
    assert not inventory.resource_graph
    assert [method.client_path for method in inventory.api_methods] == ["client.ping"]
    assert len(inventory.callable_outputs) == len(inventory.source.methods)


@given(width=st.integers(min_value=1, max_value=3), depth=st.integers(1, 4))
def test_resource_dag_paths_match_cartesian_product(width: int, depth: int) -> None:
    inventory = _inventory(_resource_source(width, depth))
    expected = {
        "client." + ".".join((*branches, "fetch"))
        for branches in product(
            (f"branch_{index}" for index in range(width)), repeat=depth
        )
    }
    assert not inventory.issues
    assert {method.client_path for method in inventory.api_methods} == expected
    assert len(inventory.api_methods) == width**depth
    assert len({method.method_id for method in inventory.api_methods}) == 1
    output = next(
        item
        for item in inventory.callable_outputs
        if item.callable_id == inventory.api_methods[0].method_id
    )
    assert output.model_ids == ("sample.Payload",)
    assert output.fields[0].name == "value"


@given(max_paths=st.integers(1, 50), max_depth=st.integers(0, 5))
def test_graph_budgets_bound_paths_without_dropping_definition_outputs(
    max_paths: int, max_depth: int
) -> None:
    inventory = _inventory(
        _resource_source(2, 5), max_paths=max_paths, max_depth=max_depth
    )
    assert len(inventory.resource_graph) < max_paths
    assert all(
        edge.target_path.count(".") <= max_depth for edge in inventory.resource_graph
    )
    assert {output.callable_id for output in inventory.callable_outputs} == {
        method.id for method in inventory.source.methods
    }
    assert len(inventory.source.methods) == 11
    assert any(
        issue.kind in {"path_limit", "resource_depth_limit", "surface_budget_limit"}
        for issue in inventory.issues
    )


@given(
    names=st.lists(
        st.text(alphabet="abcdefghijk", min_size=1, max_size=9),
        min_size=1,
        max_size=20,
        unique=True,
    ),
    blank_lines=st.integers(0, 8),
)
def test_nonsemantic_source_padding_preserves_callable_contracts(
    names: list[str], blank_lines: int
) -> None:
    declarations = "\n".join(
        f"    def method_{name}(self, value: int, /, *, flag: bool = False) -> str: ..."
        for name in names
    )
    source = f"class Client:\n{declarations}\n"
    baseline = _inventory(source)
    padded = _inventory("# generated comment\n" + "\n" * blank_lines + source)

    def contracts(inventory: Inventory) -> set[tuple[object, ...]]:
        return {
            (
                method.name,
                method.binding,
                method.parameters,
                method.return_annotation,
                method.docstring,
            )
            for method in inventory.source.methods
        }

    assert contracts(baseline) == contracts(padded)
    assert {method.client_path for method in baseline.api_methods} == {
        method.client_path for method in padded.api_methods
    }
