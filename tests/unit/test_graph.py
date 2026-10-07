"""Exercise static symbol resolution, Python inheritance, and traversal bounds."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdk_atlas import graph as graph_module
from sdk_atlas.graph import resolve_inventory
from sdk_atlas.models import (
    ClassRecord,
    DynamicMember,
    ImportBinding,
    MethodRecord,
    ModuleRecord,
    SourceInventory,
    SourceReference,
)
from sdk_atlas.scanner import scan_source


def _class(name: str, *bases: str, module: str = "future.core") -> ClassRecord:
    """Build a source class with stable locations and fully qualified identity."""
    return ClassRecord(
        id=f"{module}.{name}",
        module=module,
        name=name,
        qualname=name,
        bases=bases,
        decorators=(),
        docstring="",
        file="core.py",
        line=1,
    )


def _method(
    owner: ClassRecord,
    name: str,
    *,
    target: str = "",
    annotation: str = "",
    overload: bool = False,
    line: int = 2,
    delegates: tuple[str, ...] = (),
) -> MethodRecord:
    """Build one callable or descriptor variant without an SDK dependency."""
    return MethodRecord(
        id=f"{owner.id}.{name}:{line}",
        class_id=owner.id,
        name=name,
        signature="(self)",
        return_annotation=annotation,
        parameters=(),
        decorators=("property",) if target or annotation else (),
        docstring="",
        file="core.py",
        line=line,
        end_line=line + 1,
        is_async=False,
        is_property=bool(target or annotation),
        is_overload=overload,
        returns=(target,) if target else (),
        delegates=delegates,
    )


def _source(
    classes: tuple[ClassRecord, ...],
    methods: tuple[MethodRecord, ...] = (),
    *,
    imports: tuple[ImportBinding, ...] = (),
    exports: tuple[str, ...] = (),
    extra_modules: tuple[ModuleRecord, ...] = (),
    members: tuple[DynamicMember, ...] = (),
    support_sources: tuple[SourceReference, ...] = (),
    package_imports: tuple[ImportBinding, ...] = (),
) -> SourceInventory:
    """Build an isolated inventory with package-level reexport evidence."""
    return SourceInventory(
        package="future",
        root="unused",
        modules=(
            ModuleRecord(
                name="future",
                file="__init__.py",
                is_package=True,
                imports=tuple(
                    (
                        *package_imports,
                        *(
                            ImportBinding(name, f"future.core.{name}")
                            for name in exports
                        ),
                    )
                ),
                exports=exports,
            ),
            ModuleRecord("future.core", "core.py", False, imports),
            *extra_modules,
        ),
        classes=classes,
        methods=methods,
        dynamic_members=members,
        support_sources=support_sources,
    )


def test_discovers_future_resources_through_chained_public_reexports() -> None:
    client = _class("Entry")
    resource = _class("Quantum", module="future.quantum")
    source = _source(
        (client, resource),
        (_method(client, "unknown_tools", target="Alias"), _method(resource, "create")),
        imports=(ImportBinding("Alias", "future.resources.Exported"),),
        exports=("Entry",),
        extra_modules=(
            ModuleRecord(
                "future.resources",
                "resources/__init__.py",
                True,
                (ImportBinding("Exported", resource.id),),
            ),
        ),
    )

    inventory = resolve_inventory(source)

    assert [method.client_path for method in inventory.api_methods] == [
        "client.unknown_tools.create"
    ]
    assert inventory.resource_graph[0].target_class == resource.id
    assert inventory.api_methods[0].root_class == client.id


def test_c3_diamond_uses_later_direct_base_override_before_shared_ancestor() -> None:
    ancestor = _class("Ancestor")
    left = _class("Left", "Ancestor")
    right = _class("Right", "Ancestor")
    leaf = _class("Leaf", "Left", "Right")
    source = _source(
        (ancestor, left, right, leaf),
        (_method(ancestor, "run"), _method(right, "run")),
    )

    inventory = resolve_inventory(source, root_classes=(leaf.id,))

    assert len(inventory.api_methods) == 1
    assert inventory.api_methods[0].defined_in == right.id
    assert inventory.api_methods[0].inherited


def test_generic_base_keeps_inherited_overload_variants_and_own_shadowing() -> None:
    base = _class("Base")
    child = _class("Child", "ImportedBase[Result]")
    methods = (
        _method(base, "run", overload=True, line=2),
        _method(base, "run", overload=True, line=3),
        _method(base, "run", line=4),
        _method(base, "copy"),
        _method(child, "copy", delegates=("run",)),
    )
    source = _source(
        (base, child), methods, imports=(ImportBinding("ImportedBase", base.id),)
    )

    inventory = resolve_inventory(source, root_classes=(child.id,))

    assert [method.method_id for method in inventory.api_methods] == [
        methods[4].id,
        methods[0].id,
        methods[1].id,
        methods[2].id,
    ]
    assert inventory.api_methods[0].method_kind == "delegating_helper"


def test_type_checking_relative_imports_resolve_without_executing_source(
    tmp_path: Path,
) -> None:
    package = tmp_path / "future"
    package.mkdir()
    (package / "__init__.py").write_text("from .core import Entry\n", encoding="utf-8")
    (package / "base.py").write_text(
        "class Base:\n    def inherited(self): ...\n", encoding="utf-8"
    )
    (package / "resources.py").write_text(
        "class Resource:\n    def create(self): ...\n", encoding="utf-8"
    )
    (package / "core.py").write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .base import Base as GenericAlias\n"
        "    from .resources import Resource\n"
        "class Entry(GenericAlias[int]):\n"
        "    @property\n"
        "    def future_items(self) -> Resource:\n"
        "        raise RuntimeError('source must never run')\n",
        encoding="utf-8",
    )

    inventory = resolve_inventory(scan_source(package, "future"))

    assert {method.client_path for method in inventory.api_methods} == {
        "client.inherited",
        "client.future_items.create",
    }


def test_dynamic_member_inherits_and_shadows_a_method_of_the_same_name() -> None:
    base = _class("Base")
    child = _class("Child", "Base")
    target = _class("Resource")
    member = DynamicMember(
        "member:0", base.id, "items", "Resource(self)", "", ("Resource",), "core.py", 5
    )
    source = _source(
        (base, child, target),
        (_method(base, "items"), _method(target, "fetch")),
        members=(member,),
    )

    inventory = resolve_inventory(source, root_classes=(child.id,))

    assert [method.client_path for method in inventory.api_methods] == [
        "client.items.fetch"
    ]
    assert inventory.resource_graph[0].kind == "dynamic_member"


def test_annotation_union_resolves_references_without_container_edges() -> None:
    client = _class("Client")
    resource = _class("Resource")
    source = _source(
        (client, resource),
        (
            _method(client, "optional", annotation='Optional["Resource"]'),
            _method(client, "union", annotation="Resource | None"),
            _method(client, "collection", annotation="list[Resource]"),
            _method(resource, "fetch"),
        ),
    )

    inventory = resolve_inventory(source, root_classes=(client.id,))

    assert {edge.member for edge in inventory.resource_graph} == {"optional", "union"}


def test_alias_cycles_and_unresolved_bases_produce_bounded_inventory() -> None:
    client = _class("Client", "First")
    source = _source(
        (client,),
        (_method(client, "items", target="First"), _method(client, "ping")),
        imports=(
            ImportBinding("First", "future.core.Second"),
            ImportBinding("Second", "future.core.First"),
        ),
    )

    inventory = resolve_inventory(source, root_classes=(client.id,))

    assert not inventory.resource_graph
    assert [method.client_path for method in inventory.api_methods] == ["client.ping"]
    assert any(issue.kind == "unresolved_base" for issue in inventory.issues)


def test_growing_alias_cycles_terminate_without_inventing_a_target() -> None:
    client = _class("Client", "Loop")
    source = _source(
        (client,),
        (_method(client, "ping"),),
        imports=(ImportBinding("Loop", "Loop.Child", "alias"),),
    )

    inventory = resolve_inventory(source, root_classes=(client.id,))

    assert len(inventory.api_methods) == 1
    assert any(issue.kind == "unresolved_base" for issue in inventory.issues)


def test_exported_callable_class_remains_a_root_beside_resource_clients() -> None:
    client = _class("Client")
    resource = _class("Resource")
    model = _class("Model")
    source = _source(
        (client, resource, model),
        (
            _method(client, "items", target="Resource"),
            _method(resource, "create"),
            _method(model, "generate"),
        ),
        exports=("Client", "Model"),
    )

    inventory = resolve_inventory(source)

    assert {method.root_class for method in inventory.api_methods} == {
        client.id,
        model.id,
    }


def test_explicit_root_caches_only_reachable_inherited_callables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _class("Base")
    children = tuple(_class(f"Child{index}", "Base") for index in range(200))
    methods = tuple(_method(base, f"method_{index}") for index in range(200))
    source = _source((base, *children), methods)
    resolvers: list[graph_module._Resolver] = []

    class ObservedResolver(graph_module._Resolver):
        """Expose retained references to enforce the selected-root memory bound."""

        def __init__(
            self, source: SourceInventory, max_depth: int, budget: int
        ) -> None:
            super().__init__(source, max_depth, budget)
            resolvers.append(self)

    monkeypatch.setattr(graph_module, "_Resolver", ObservedResolver)

    inventory = resolve_inventory(source, root_classes=(children[0].id,))

    assert len(inventory.api_methods) == 200
    cache = resolvers[0].effective_methods
    assert set(cache) == {children[0].id}
    assert sum(len(records) for records in cache.values()) == 200


def test_effective_member_expansion_is_bounded_and_diagnosed() -> None:
    base = _class("Base")
    child = _class("Child", "Base")
    methods = tuple(_method(base, f"method_{index}") for index in range(100))
    source = _source((base, child), methods)

    inventory = resolve_inventory(source, root_classes=(child.id,), max_paths=20)

    assert len(inventory.api_methods) == 20
    assert any(issue.kind == "surface_budget_limit" for issue in inventory.issues)


def test_callable_override_shadows_inherited_resource_property() -> None:
    base = _class("Base")
    child = _class("Child", "Base")
    resource = _class("Resource")
    source = _source(
        (base, child, resource),
        (_method(base, "items", target="Resource"), _method(child, "items")),
    )

    inventory = resolve_inventory(source, root_classes=(child.id,))

    assert not inventory.resource_graph
    assert [method.client_path for method in inventory.api_methods] == ["client.items"]


def test_builtin_type_bases_and_imported_builtin_aliases_are_resolved() -> None:
    error = _class("Error", "Exception")
    container = _class("Container", "dict[str, int]")
    alias = _class("AliasError", "LocalAlias")
    source = _source(
        (error, container, alias),
        imports=(
            ImportBinding("Imported", "builtins.Exception"),
            ImportBinding("LocalAlias", "Imported", "alias"),
        ),
    )

    inventory = resolve_inventory(source)

    assert not inventory.issues


def test_discovered_local_type_shadows_a_builtin_type_name() -> None:
    local = _class("Exception")
    child = _class("Child", "Exception")
    source = _source((local, child), (_method(local, "handle"),))

    inventory = resolve_inventory(source, root_classes=(child.id,))

    assert not inventory.issues
    assert inventory.api_methods[0].defined_in == local.id
    assert inventory.api_methods[0].inherited


def test_local_assignment_shadows_builtin_fallback_without_hiding_a_missing_base() -> (
    None
):
    child = _class("Child", "Exception")
    source = _source(
        (child,), imports=(ImportBinding("Exception", "Missing", "alias"),)
    )

    inventory = resolve_inventory(source)

    assert [issue.kind for issue in inventory.issues] == ["unresolved_base"]


def test_real_external_import_shadows_a_builtin_name() -> None:
    child = _class("Child", "Exception")
    source = _source(
        (child,), imports=(ImportBinding("Exception", "arbitrary_runtime.Exception"),)
    )

    inventory = resolve_inventory(source)

    assert [issue.kind for issue in inventory.issues] == ["external_base"]
    assert "arbitrary_runtime.Exception" in inventory.issues[0].message


def test_bare_import_of_builtin_named_module_remains_an_external_boundary() -> None:
    child = _class("Child", "Exception")
    source = _source((child,), imports=(ImportBinding("Exception", "Exception"),))

    inventory = resolve_inventory(source)

    assert [issue.kind for issue in inventory.issues] == ["external_base"]


def test_standard_library_and_arbitrary_imported_bases_are_explicit_boundaries() -> (
    None
):
    generic = _class("GenericChild", "Generic[T]")
    typed = _class("Record", "TypedDict")
    abstract = _class("Abstract", "abc.ABC")
    model = _class("Model", "BaseModel")
    source = _source(
        (generic, typed, abstract, model),
        imports=(
            ImportBinding("Generic", "typing.Generic"),
            ImportBinding("TypedDict", "typing.TypedDict"),
            ImportBinding("abc", "abc"),
            ImportBinding("BaseModel", "arbitrary_validation.BaseModel"),
        ),
    )

    inventory = resolve_inventory(source)

    assert len(inventory.issues) == 4
    assert all(issue.kind == "external_base" for issue in inventory.issues)
    for canonical in (
        "typing.Generic",
        "typing.TypedDict",
        "abc.ABC",
        "arbitrary_validation.BaseModel",
    ):
        issue = next(issue for issue in inventory.issues if canonical in issue.message)
        assert "--dependency " in issue.message
        assert "--dependency-source " in issue.message


def test_assignment_alias_follows_proven_external_module_import() -> None:
    child = _class("Child", "Alias")
    source = _source(
        (child,),
        imports=(
            ImportBinding("external_module", "arbitrary_runtime"),
            ImportBinding("Alias", "external_module.Base", "alias"),
        ),
    )

    inventory = resolve_inventory(source)

    assert [issue.kind for issue in inventory.issues] == ["external_base"]
    assert "arbitrary_runtime.Base" in inventory.issues[0].message


def test_unbound_alias_attribute_root_and_missing_local_import_stay_unresolved() -> (
    None
):
    alias = _class("AliasChild", "Alias")
    imported = _class("ImportedChild", "Imported")
    unbound = _class("UnboundChild", "Ghost.Base")
    source = _source(
        (alias, imported, unbound),
        imports=(
            ImportBinding("Alias", "Ghost.Missing", "alias"),
            ImportBinding("Imported", "future.missing.Base"),
        ),
    )

    inventory = resolve_inventory(source)

    assert len(inventory.issues) == 3
    assert all(issue.kind == "unresolved_base" for issue in inventory.issues)


def test_dependency_methods_inherit_without_creating_a_fallback_root() -> None:
    dependency = _class("Base", module="arbitrary_runtime.core")
    child = _class("Child", "Imported")
    source = _source(
        (dependency, child),
        (_method(dependency, "inherited"),),
        imports=(ImportBinding("Imported", dependency.id),),
        extra_modules=(ModuleRecord(dependency.module, "dependency/core.py", False),),
        support_sources=(SourceReference("arbitrary_runtime", "dependency"),),
    )

    inventory = resolve_inventory(source)

    assert not inventory.issues
    assert len(inventory.api_methods) == 1
    assert inventory.api_methods[0].root_class == child.id
    assert inventory.api_methods[0].defined_in == dependency.id
    assert inventory.api_methods[0].inherited


def test_missing_class_in_included_dependency_stays_unresolved() -> None:
    child = _class("Child", "Imported")
    source = _source(
        (child,),
        imports=(ImportBinding("Imported", "arbitrary_runtime.Missing"),),
        support_sources=(SourceReference("arbitrary_runtime", "dependency"),),
    )

    inventory = resolve_inventory(source)

    assert [issue.kind for issue in inventory.issues] == ["unresolved_base"]


def test_package_export_can_deliberately_select_an_included_dependency_class() -> None:
    dependency = _class("Base", module="arbitrary_runtime.core")
    source = _source(
        (dependency,),
        (_method(dependency, "run"),),
        imports=(ImportBinding("ExternalAlias", dependency.id),),
        exports=("ExternalAlias",),
        support_sources=(SourceReference("arbitrary_runtime", "dependency"),),
    )

    inventory = resolve_inventory(source)

    assert len(inventory.api_methods) == 1
    assert inventory.api_methods[0].root_class == dependency.id


def test_dependency_imported_only_as_a_base_does_not_become_an_inferred_root() -> None:
    dependency = _class("Base", module="arbitrary_runtime.core")
    child = _class("Child", "Parent", module="future")
    source = _source(
        (dependency, child),
        (_method(dependency, "inherited"), _method(child, "own")),
        support_sources=(SourceReference("arbitrary_runtime", "dependency"),),
        package_imports=(ImportBinding("Parent", dependency.id),),
    )

    inventory = resolve_inventory(source)

    assert {method.root_class for method in inventory.api_methods} == {child.id}
    assert {method.client_path for method in inventory.api_methods} == {
        "client.own",
        "client.inherited",
    }


def test_scanned_import_provenance_separates_external_aliases_from_unknown_names(
    tmp_path: Path,
) -> None:
    package = tmp_path / "future"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from typing import Generic\n"
        "from arbitrary_runtime import Base as Imported\n"
        "import builtins as runtime_builtins\n"
        "ExternalAlias = Imported\n"
        "UnknownAlias = Ghost.Missing\n"
        "class Entry(ExternalAlias, Generic[T]):\n"
        "    def run(self): ...\n"
        "class Error(runtime_builtins.Exception): ...\n"
        "class Broken(UnknownAlias): ...\n",
        encoding="utf-8",
    )

    inventory = resolve_inventory(scan_source(package, "future"))

    assert [issue.kind for issue in inventory.issues] == [
        "external_base",
        "external_base",
        "unresolved_base",
    ]
    assert "arbitrary_runtime.Base" in inventory.issues[0].message
    assert "typing.Generic" in inventory.issues[1].message


@pytest.mark.parametrize(
    "declaration, base, unresolved",
    (
        ("str = factory()", "str", True),
        ("def list(): ...", "list", True),
        ("Exception: type", "Exception", False),
    ),
)
def test_module_values_shadow_builtins_but_annotation_only_names_do_not(
    tmp_path: Path, declaration: str, base: str, unresolved: bool
) -> None:
    package = tmp_path / "future"
    package.mkdir()
    (package / "__init__.py").write_text(
        f"{declaration}\nclass Child({base}): ...\n", encoding="utf-8"
    )

    inventory = resolve_inventory(scan_source(package, "future"))

    assert bool(inventory.issues) is unresolved
    if unresolved:
        assert inventory.issues[0].kind == "unresolved_base"


def test_reached_method_preserves_arguments_and_declared_response_fields(
    tmp_path: Path,
) -> None:
    package = tmp_path / "future"
    package.mkdir()
    (package / "__init__.py").write_text(
        "class Result:\n"
        "    count: int\n"
        "class Entry:\n"
        "    def generate(self, prompt: str, *, limit: int = 10) -> Result: ...\n",
        encoding="utf-8",
    )

    inventory = resolve_inventory(scan_source(package, "future"))

    assert [method.client_path for method in inventory.api_methods] == [
        "client.generate"
    ]
    method = inventory.source.methods[0]
    assert [
        (parameter.name, parameter.annotation) for parameter in method.parameters
    ] == [
        ("self", ""),
        ("prompt", "str"),
        ("limit", "int"),
    ]
    output = inventory.callable_outputs[0]
    assert output.callable_id == method.id
    assert output.annotation == "Result"
    assert output.model_ids == ("future.Result",)
    assert output.status == "resolved"
    assert [(field.name, field.annotation) for field in output.fields] == [
        ("count", "int")
    ]


def test_inheritance_and_resource_cycles_are_diagnosed_without_recursion() -> None:
    left = _class("Left", "Right")
    right = _class("Right", "Left")
    source = _source(
        (left, right),
        (_method(left, "right", target="Right"), _method(right, "left", target="Left")),
    )

    inventory = resolve_inventory(source, root_classes=(left.id,))

    assert len(inventory.resource_graph) <= 3
    assert {issue.kind for issue in inventory.issues} >= {
        "inheritance_cycle",
        "resource_cycle",
    }


def test_inconsistent_python_mro_is_reported() -> None:
    a = _class("A")
    b = _class("B")
    x = _class("X", "A", "B")
    y = _class("Y", "B", "A")
    invalid = _class("Invalid", "X", "Y")
    source = _source((a, b, x, y, invalid))

    inventory = resolve_inventory(source, root_classes=(invalid.id,))

    assert any(issue.kind == "inconsistent_mro" for issue in inventory.issues)


def test_depth_and_path_budgets_are_explicit() -> None:
    client = _class("Client")
    middle = _class("Middle")
    leaf = _class("Leaf")
    source = _source(
        (client, middle, leaf),
        (
            _method(client, "middle", target="Middle"),
            _method(middle, "leaf", target="Leaf"),
        ),
    )

    depth_limited = resolve_inventory(source, root_classes=(client.id,), max_depth=1)
    path_limited = resolve_inventory(source, root_classes=(client.id,), max_paths=1)

    assert len(depth_limited.resource_graph) == 1
    assert any(issue.kind == "resource_depth_limit" for issue in depth_limited.issues)
    assert not path_limited.resource_graph
    assert any(issue.kind == "path_limit" for issue in path_limited.issues)


def test_deep_inheritance_is_truncated_with_a_diagnostic() -> None:
    base = _class("Base")
    middle = _class("Middle", "Base")
    leaf = _class("Leaf", "Middle")
    source = _source((leaf, middle, base), (_method(base, "run"),))

    inventory = resolve_inventory(source, root_classes=(leaf.id,), max_depth=1)

    assert not inventory.api_methods
    assert any(issue.kind == "inheritance_depth_limit" for issue in inventory.issues)


@pytest.mark.parametrize("bound", ({"max_depth": -1}, {"max_paths": 0}))
def test_invalid_bounds_raise_actionable_errors(bound: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="max_depth"):
        resolve_inventory(_source(()), **bound)


def test_unknown_explicit_root_raises_actionable_error() -> None:
    with pytest.raises(ValueError, match="Unknown root class"):
        resolve_inventory(_source(()), root_classes=("missing.Client",))
