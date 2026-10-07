"""Verify source evidence independently of any vendor's SDK surface."""

import ast
import os
import tracemalloc
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdk_atlas.scanner import scan_source


def test_methods_preserve_overloads_parameter_kinds_and_async(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": '''
from typing import overload
class Surface:
    """Public surface documentation."""
    @overload
    def run(self, value: str, /, *, count: int = 2) -> str: ...
    @overload
    def run(self, value: bytes, /, *, count: int = 2) -> bytes: ...
    async def run(self, value, /, *items, required, count=2, **options):
        """Execute the operation."""
        return await self._post(f"/future/{value}")
    def _hidden(self):
        pass
'''
        }
    )
    result = scan_source(root, "sample")
    assert not result.issues
    assert len(result.classes) == 1
    assert len(result.methods) == 3
    assert [method.is_overload for method in result.methods] == [True, True, False]
    method = result.methods[-1]
    assert method.is_async
    assert method.docstring == "Execute the operation."
    assert [param.kind for param in method.parameters] == [
        "POSITIONAL_ONLY",
        "POSITIONAL_ONLY",
        "VAR_POSITIONAL",
        "KEYWORD_ONLY",
        "KEYWORD_ONLY",
        "VAR_KEYWORD",
    ]
    assert method.parameters[3].required
    assert method.parameters[4].default == "2"
    assert method.transport[0].route == "/future/{value}"
    assert method.transport[0].operation == "POST"


def test_nested_scopes_do_not_pollute_method_evidence(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
class Surface:
    def helper(self):
        def nested():
            self._post("/nested")
        hidden = lambda: self._post("/lambda")
        class Nested:
            def run(self):
                self._post("/inner-class")
        return self.run()
    def run(self):
        return self._get("/visible")
"""
        }
    )
    source = scan_source(root, "sample")
    assert len(source.classes) == 1
    assert not source.methods[0].transport
    assert source.methods[0].delegates == ("run",)
    assert source.methods[1].transport[0].route == "/visible"


def test_constructor_assignments_properties_and_guarded_relative_imports(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": "from .client import Client\n",
            "client.py": """
from typing import TYPE_CHECKING
from functools import cached_property
if TYPE_CHECKING:
    from .resource import Resource as R
Alias = R
class Client:
    def __init__(self):
        self.dynamic = Alias(self)
        self.left, self.right = R(self), R(self)
        self._private = R(self)
    @cached_property
    def resource(self) -> "R":
        return R(self)
""",
            "resource.py": "class Resource:\n    def call(self): pass\n",
        }
    )
    source = scan_source(root, "sample")
    client_module = next(
        module for module in source.modules if module.name == "sample.client"
    )
    assert {binding.name: binding.target for binding in client_module.imports}[
        "R"
    ] == "sample.resource.Resource"
    assert {member.name for member in source.dynamic_members} == {
        "dynamic",
        "left",
        "right",
    }
    prop = next(method for method in source.methods if method.name == "resource")
    assert prop.is_property
    assert prop.returns == ("R",)


def test_transport_variable_routes_and_explicit_request_verb(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
class Surface:
    def call(self, route):
        self.transport.request("PATCH", route)
        self._get(route)
        self.transport.request(method="DELETE", url=f"/items/{route}")
        self.helper("/items/{}".format(route))
"""
        }
    )
    evidence = scan_source(root, "sample").methods[0].transport
    assert [(item.operation, item.route_kind) for item in evidence] == [
        ("PATCH", "expression"),
        ("GET", "expression"),
        ("DELETE", "formatted"),
        ("unknown", "expression"),
    ]


def test_syntax_and_size_failures_are_diagnosed(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {"__init__.py": "pass\n", "broken.py": "class Broken(\n", "large.py": "#" * 64}
    )
    source = scan_source(root, "sample", max_source_bytes=32)
    assert [issue.kind for issue in source.issues] == [
        "source_error",
        "source_size_limit",
    ]
    assert [module.name for module in source.modules] == ["sample"]
    with pytest.raises(ValueError, match="file limit"):
        scan_source(root, "sample", max_files=1)


def test_single_file_module_uses_requested_module_identity(tmp_path: Path) -> None:
    path = tmp_path / "module.py"
    path.write_text("class Surface:\n    def call(self): pass\n", encoding="utf-8")
    result = scan_source(path, "vendor.module")
    assert result.modules[0].name == "vendor.module"
    assert result.classes[0].id == "vendor.module.Surface"
    assert result.classes[0].file == "module.py"


def test_declared_source_encoding_and_ast_limit(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    content = "# coding: latin-1\nclass Caf\N{LATIN SMALL LETTER E WITH ACUTE}: pass\n"
    root = make_package({"__init__.py": content})
    (root / "__init__.py").write_bytes(content.encode("latin-1"))
    assert (
        scan_source(root, "sample").classes[0].name
        == "Caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    )
    assert (
        scan_source(root, "sample", max_ast_nodes=1).issues[0].kind == "ast_size_limit"
    )


def test_module_functions_constants_aliases_and_native_stubs(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": (
                "from .native import array\nPI: float = 3.14\nalias = array\n"
            ),
            "native.pyi": """
from typing import overload
@overload
def array(value: int, /) -> int: ...
@overload
def array(value: list[int], /) -> list[int]: ...
class Shape:
    def size(self) -> int: ...
""",
        }
    )
    (root / "native.cp313-win_amd64.pyd").write_bytes(b"opaque binary fixture")
    source = scan_source(root, "sample")
    assert len(source.functions) == 2
    assert all(function.is_overload for function in source.functions)
    native = next(module for module in source.modules if module.name == "sample.native")
    assert "native" in native.source_kind and "stub" in native.source_kind
    assert (
        len([module for module in source.modules if module.name == "sample.native"])
        == 1
    )
    assert {member.name for member in source.module_members} >= {"PI", "alias", "array"}
    assert source.issues[0].kind == "native_module"


def test_source_and_stub_class_records_merge_without_losing_signatures(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": "class Model:\n    def run(self, value): return value\n",
            "__init__.pyi": "class Model:\n    def run(self, value: int) -> int: ...\n",
        }
    )
    source = scan_source(root, "sample")
    assert len(source.modules) == 1
    assert len(source.classes) == 1
    assert len(source.methods) == 2
    assert len({method.id for method in source.methods}) == 2


def test_receiver_name_is_derived_from_signature(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
class Client:
    def __init__(this):
        this.resource = Resource()
    def delegate(this):
        return this.call()
class Resource: pass
"""
        }
    )
    source = scan_source(root, "sample")
    assert source.dynamic_members[0].name == "resource"
    delegate = next(method for method in source.methods if method.name == "delegate")
    assert delegate.delegates == ("call",)


def test_dictionary_get_is_not_treated_as_an_http_operation(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
def calculate(options):
    return options.get("axis", 0)
class Client:
    def fetch(self, route):
        return self._get(route)
"""
        }
    )
    source = scan_source(root, "sample")
    assert not source.functions[0].transport
    assert source.methods[0].transport[0].route == "route"


def test_native_package_initializer_maps_to_its_package(tmp_path: Path) -> None:
    root = tmp_path / "sample"
    root.mkdir()
    (root / "__init__.cpython-313-x86_64-linux-gnu.so").write_bytes(b"opaque")
    source = scan_source(root, "sample")
    assert source.modules[0].name == "sample"
    assert source.modules[0].is_package
    assert source.modules[0].source_kind == "native"


def test_decorator_aliases_and_staticmethod_receiver_are_resolved(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
from functools import cached_property as cache
from typing import overload as variant
class Surface:
    @cache
    def child(self) -> Child:
        return Child()
    @staticmethod
    def helper(value):
        value.external = Child()
    @variant
    def run(self, value: int) -> int: ...
@variant
def calculate(value: int) -> int: ...
class Child: pass
"""
        }
    )
    source = scan_source(root, "sample")
    assert source.methods[0].is_property
    assert source.methods[2].is_overload
    assert source.functions[0].is_overload
    assert not source.dynamic_members


def test_import_and_alias_provenance_survives_source_stub_merge(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
from dependency import Base
Base = Base
Alias = Base
PrivateAlias = unknown.Base
""",
            "__init__.pyi": "from other_dependency import Marker\nStubAlias = Marker\n",
        }
    )
    bindings = {
        binding.name: binding
        for binding in scan_source(root, "sample").modules[0].imports
    }
    assert bindings["Base"].kind == "import"
    assert bindings["Base"].target == "dependency.Base"
    assert bindings["Alias"].kind == "alias"
    assert bindings["PrivateAlias"].kind == "alias"
    assert bindings["Marker"].kind == "import"
    assert bindings["StubAlias"].kind == "alias"


def test_runtime_assignments_remove_shadowed_import_bindings(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
from dependency import Base, Marker
Base = factory()
Marker: object = 42
"""
        }
    )
    assert not scan_source(root, "sample").modules[0].imports


def test_module_protocol_hooks_are_retained_without_execution(
    make_package: Callable[[Mapping[str, str]], Path],
) -> None:
    root = make_package(
        {
            "__init__.py": """
def __getattr__(name: str) -> object:
    raise RuntimeError('must never execute')
def __dir__() -> list[str]: ...
def _implementation(): ...
"""
        }
    )
    assert {function.name for function in scan_source(root, "sample").functions} == {
        "__getattr__",
        "__dir__",
    }


def test_small_source_does_not_allocate_the_configured_maximum(tmp_path: Path) -> None:
    module = tmp_path / "sample.py"
    module.write_text(
        "def calculate(value: int) -> int: return value\n", encoding="utf-8"
    )
    tracemalloc.start()
    try:
        source = scan_source(module, "sample", max_source_bytes=64 * 1024 * 1024)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(source.functions) == 1
    # A generous whole-scan memory budget catches a maximum-sized read buffer
    # without depending on allocator details or the number of internal calls.
    assert peak < 16 * 1024 * 1024


def test_source_change_during_read_is_reported_without_partial_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = tmp_path / "sample.py"
    module.write_text("class Client: pass\n", encoding="utf-8")
    before = module.stat()
    after = SimpleNamespace(
        st_size=before.st_size + 1, st_mtime_ns=before.st_mtime_ns + 1
    )
    results = iter((before, after))
    monkeypatch.setattr(os, "fstat", lambda descriptor: next(results))
    source = scan_source(module, "sample")
    assert not source.modules and not source.classes
    assert source.issues[0].kind == "source_changed"


@pytest.mark.parametrize(
    "code",
    [
        "values = [value * 2 for value in range(4)]",
        "class Result:\n    value: list[int]\n    def run(self, *args, **kw): ...",
        "match value:\n    case Result(value=item): pass",
        "async def run(value):\n    return await value()",
        "def render(value):\n    return f'{value!r:>10}'",
    ],
)
def test_iterative_ast_budget_matches_standard_traversal_boundaries(code: str) -> None:
    from sdk_atlas.scanner import _ast_size_exceeded

    tree = ast.parse(code)
    size = sum(1 for _ in ast.walk(tree))
    assert not _ast_size_exceeded(tree, size)
    assert _ast_size_exceeded(tree, size - 1)
