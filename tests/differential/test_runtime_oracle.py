"""Compare static evidence with CPython on an explicitly controlled fixture.

Only the literal fixture and constrained source generated here are executed.
The scanner never imports target packages; this oracle is not a runtime mode
and must never receive arbitrary SDK files or fuzz corpus bytes.
"""

import inspect
import types
import typing
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given
from hypothesis import strategies as st

from sdk_atlas.graph import resolve_inventory
from sdk_atlas.models import Inventory, Parameter
from sdk_atlas.scanner import scan_source

_FIXTURE = '''
from typing import ClassVar, NamedTuple, NotRequired, Required, TypedDict

class Parent:
    identifier: int
    label: str = "default"
    category: ClassVar[str] = "metadata"

class Result(Parent):
    label: str
    count: int = 0

class Detail(TypedDict, total=False):
    optional: str
    mandatory: Required[int]
    extra: NotRequired[str]

class Pair(NamedTuple):
    left: int
    right: str

class Ancestor:
    def run(self, token: str) -> Result:
        """Ancestor implementation."""
        raise RuntimeError("Methods are not executed by this oracle")

class Left(Ancestor):
    pass

class Right(Ancestor):
    def run(self, token: int, *, retry: bool = False) -> Result:
        """Right-hand override selected by C3."""
        raise RuntimeError("Methods are not executed by this oracle")

class Client(Left, Right):
    def __init__(this):
        pass

    def fetch(this, value: int, /, option: str = "x", *values: float,
              required: bool, enabled: bool = True, **extra: int) -> Result:
        """Fetch with all Python parameter kinds."""
        raise RuntimeError("Methods are not executed by this oracle")

    @classmethod
    def from_value(owner, value: int = 3, /, *, label: str = "hello") -> Result:
        """Build using a renamed class receiver."""
        raise RuntimeError("Methods are not executed by this oracle")

    @staticmethod
    def static(self: int, /, *, flag: bool = False) -> int:
        """The parameter named self remains a caller argument."""
        raise RuntimeError("Methods are not executed by this oracle")

    async def async_fetch(self, *, key: str) -> Detail:
        """An asynchronous callable still has a synchronous signature."""
        raise RuntimeError("Methods are not executed by this oracle")

    @property
    def result(self) -> Result:
        raise RuntimeError("Properties are not evaluated by this oracle")

    def pair(self) -> Pair:
        raise RuntimeError("Methods are not executed by this oracle")
'''


def _controlled_fixture(code: str) -> tuple[types.ModuleType, Inventory]:
    """Execute only locally constructed fixture source, never scanner input files."""
    module = types.ModuleType("oracle_fixture")
    exec(compile(code, "<controlled-oracle-fixture>", "exec"), module.__dict__)
    with TemporaryDirectory(prefix="sdk-atlas-oracle-") as directory:
        path = Path(directory) / "oracle_fixture.py"
        path.write_text(code, encoding="utf-8")
        inventory = resolve_inventory(
            scan_source(path, "oracle_fixture"), root_classes=("Client",)
        )
    return module, inventory


def _annotation_text(annotation: object) -> str:
    if annotation is inspect.Signature.empty:
        return ""
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation).removeprefix("typing.")


def _runtime_parameters(callable_object: object) -> tuple[Parameter, ...]:
    signature = inspect.signature(callable_object)
    return tuple(
        Parameter(
            parameter.name,
            parameter.kind.name,
            _annotation_text(parameter.annotation),
            ""
            if parameter.default is inspect.Parameter.empty
            else repr(parameter.default),
            parameter.default is inspect.Parameter.empty
            and parameter.kind
            not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD},
        )
        for parameter in signature.parameters.values()
    )


def test_method_binding_signatures_docs_and_c3_match_runtime() -> None:
    module, inventory = _controlled_fixture(_FIXTURE)
    client_type = module.Client
    instance = client_type()
    methods = {method.id: method for method in inventory.source.methods}
    effective = {method.method_name: method for method in inventory.api_methods}
    expected_names = {
        "fetch",
        "from_value",
        "static",
        "async_fetch",
        "pair",
        "run",
    }
    assert set(effective) == expected_names
    for name, api_method in effective.items():
        extracted = methods[api_method.method_id]
        runtime = getattr(instance, name)
        declaration = inspect.getattr_static(client_type, name)
        expected_binding = (
            "class"
            if isinstance(declaration, classmethod)
            else "static"
            if isinstance(declaration, staticmethod)
            else "instance"
        )
        assert extracted.binding == expected_binding
        assert tuple(
            parameter for parameter in extracted.parameters if not parameter.implicit
        ) == _runtime_parameters(runtime)
        assert extracted.return_annotation == _annotation_text(
            inspect.signature(runtime).return_annotation
        )
        assert extracted.docstring == (inspect.getdoc(runtime) or "")
        assert extracted.is_async == inspect.iscoroutinefunction(runtime)
        defining_class = next(
            owner for owner in client_type.__mro__ if name in vars(owner)
        )
        assert api_method.defined_in == f"oracle_fixture.{defining_class.__name__}"
        assert api_method.inherited == (defining_class is not client_type)

    constructor = next(
        method
        for method in inventory.source.methods
        if method.class_id == "oracle_fixture.Client" and method.name == "__init__"
    )
    assert tuple(
        parameter for parameter in constructor.parameters if not parameter.implicit
    ) == _runtime_parameters(instance.__init__)

    property_method = next(
        method
        for method in inventory.source.methods
        if method.class_id == "oracle_fixture.Client" and method.name == "result"
    )
    descriptor = inspect.getattr_static(client_type, "result")
    assert isinstance(descriptor, property)
    assert property_method.is_property
    assert property_method.return_annotation == _annotation_text(
        inspect.signature(descriptor.fget).return_annotation
    )


def test_response_fields_match_runtime_annotations_and_required_keys() -> None:
    module, inventory = _controlled_fixture(_FIXTURE)
    methods = {method.id: method for method in inventory.source.methods}
    outputs = {
        methods[output.callable_id].name: output
        for output in inventory.callable_outputs
        if methods[output.callable_id].class_id == "oracle_fixture.Client"
    }
    for method_name, model_name in [
        ("fetch", "Result"),
        ("async_fetch", "Detail"),
        ("pair", "Pair"),
    ]:
        model = getattr(module, model_name)
        hints = typing.get_type_hints(model, include_extras=True)
        expected_hints = {
            name: annotation
            for name, annotation in hints.items()
            if typing.get_origin(annotation) is not typing.ClassVar
        }
        output = outputs[method_name]
        assert output.model_ids == (f"oracle_fixture.{model_name}",)
        fields = {field.name: field for field in output.fields}
        assert set(fields) == set(expected_hints)
        for name, annotation in expected_hints.items():
            assert fields[name].annotation == _annotation_text(annotation)
            declaring_class = next(
                owner
                for owner in model.__mro__
                if name in vars(owner).get("__annotations__", {})
            )
            if not typing.is_typeddict(model):
                assert fields[name].inherited == (declaring_class is not model)
                assert fields[name].class_id == (
                    f"oracle_fixture.{declaring_class.__name__}"
                )
            else:
                expected = "required" if name in model.__required_keys__ else "optional"
                assert fields[name].requiredness == expected
    assert outputs["fetch"].status == "resolved"
    # NamedTuple/TypedDict mix in stdlib behavior not scanned as local source.
    assert outputs["async_fetch"].status in {"resolved", "partial"}
    assert outputs["pair"].status in {"resolved", "partial"}


@given(
    binding=st.sampled_from(["instance", "class", "static"]),
    receiver=st.sampled_from(["self", "this", "owner", "kind"]),
    positional_only=st.booleans(),
    default=st.one_of(st.none(), st.booleans(), st.integers(-1000, 1000)),
    asynchronous=st.booleans(),
)
def test_generated_supported_signatures_match_bound_runtime(
    binding: str,
    receiver: str,
    positional_only: bool,
    default: object,
    asynchronous: bool,
) -> None:
    decorators = {
        "instance": "",
        "class": "    @classmethod\n",
        "static": "    @staticmethod\n",
    }
    arguments = [] if binding == "static" else [receiver]
    arguments.append(f"value: int = {default!r}")
    if positional_only:
        arguments.append("/")
    arguments.extend(["*items: str", "flag: bool = False", "**options: int"])
    definition = "async def" if asynchronous else "def"
    code = (
        "class Client:\n"
        + decorators[binding]
        + f"    {definition} operation({', '.join(arguments)}) -> int:\n"
        "        raise RuntimeError('oracle methods are never called')\n"
    )
    module, inventory = _controlled_fixture(code)
    extracted = inventory.source.methods[0]
    runtime = module.Client().operation
    assert extracted.binding == binding
    assert tuple(
        parameter for parameter in extracted.parameters if not parameter.implicit
    ) == _runtime_parameters(runtime)
    assert extracted.is_async == inspect.iscoroutinefunction(runtime)
    assert len(inventory.api_methods) == 1
    assert inventory.api_methods[0].client_path == "client.operation"
