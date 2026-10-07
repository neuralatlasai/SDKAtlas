"""Immutable records shared by source analysis, graph resolution, and exports."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScanIssue:
    """Describe incomplete static evidence without inventing SDK behavior."""

    kind: str
    message: str
    file: str = ""
    line: int = 0


@dataclass(frozen=True, slots=True)
class ImportBinding:
    """Map a local name to a target, preserving import versus alias provenance."""

    name: str
    target: str
    kind: str = "import"
    expression: str = ""


@dataclass(frozen=True, slots=True)
class ModuleRecord:
    """Describe a parsed source module and its statically known bindings."""

    name: str
    file: str
    is_package: bool
    imports: tuple[ImportBinding, ...] = ()
    exports: tuple[str, ...] = ()
    source_kind: str = "python"


@dataclass(frozen=True, slots=True)
class Parameter:
    """Preserve Python parameter kind, annotation, and default expression."""

    name: str
    kind: str
    annotation: str = ""
    default: str = ""
    required: bool = True
    implicit: bool = False


@dataclass(frozen=True, slots=True)
class ClassRecord:
    """Describe a source class; its ID is its absolute dotted qualified name."""

    id: str
    module: str
    name: str
    qualname: str
    bases: tuple[str, ...]
    decorators: tuple[str, ...]
    docstring: str
    file: str
    line: int


@dataclass(frozen=True, slots=True)
class TransportEvidence:
    """Record a path-bearing call; operation inference is explicitly best effort."""

    id: str
    method_id: str
    class_id: str
    module: str
    file: str
    line: int
    call: str
    operation: str
    route: str
    route_kind: str


@dataclass(frozen=True, slots=True)
class MethodRecord:
    """Preserve one definition, including each overload and property variant."""

    id: str
    class_id: str
    name: str
    signature: str
    return_annotation: str
    parameters: tuple[Parameter, ...]
    decorators: tuple[str, ...]
    docstring: str
    file: str
    line: int
    end_line: int
    is_async: bool
    is_property: bool
    is_overload: bool
    returns: tuple[str, ...] = ()
    delegates: tuple[str, ...] = ()
    transport: tuple[TransportEvidence, ...] = ()
    binding: str = "instance"


@dataclass(frozen=True, slots=True)
class DynamicMember:
    """Record a public self attribute assignment without executing its value."""

    id: str
    class_id: str
    name: str
    expression: str
    annotation: str
    target_refs: tuple[str, ...]
    file: str
    line: int


@dataclass(frozen=True, slots=True)
class FunctionRecord:
    """Describe a module-level callable definition from Python source or stubs."""

    id: str
    module: str
    name: str
    signature: str
    return_annotation: str
    parameters: tuple[Parameter, ...]
    decorators: tuple[str, ...]
    docstring: str
    file: str
    line: int
    end_line: int
    is_async: bool
    is_overload: bool
    transport: tuple[TransportEvidence, ...] = ()


@dataclass(frozen=True, slots=True)
class ModuleMember:
    """Preserve public bindings, including aliases and opaque native exports."""

    id: str
    module: str
    name: str
    kind: str
    expression: str
    annotation: str
    file: str
    line: int


@dataclass(frozen=True, slots=True)
class ClassField:
    """Preserve a declared data field without evaluating its default value."""

    id: str
    class_id: str
    name: str
    annotation: str
    default: str
    requiredness: str
    file: str
    line: int
    kind: str = "data"


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Locate an explicitly included support package without importing it."""

    package: str
    root: str
    version: str = "unknown"


@dataclass(frozen=True, slots=True)
class SourceInventory:
    """Own records whose locations are relative to their package's source root."""

    package: str
    root: str
    modules: tuple[ModuleRecord, ...]
    classes: tuple[ClassRecord, ...]
    methods: tuple[MethodRecord, ...]
    dynamic_members: tuple[DynamicMember, ...]
    issues: tuple[ScanIssue, ...] = ()
    version: str = "unknown"
    functions: tuple[FunctionRecord, ...] = ()
    module_members: tuple[ModuleMember, ...] = ()
    support_sources: tuple[SourceReference, ...] = ()
    class_fields: tuple[ClassField, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """Describe one resolved attribute transition on a reachable client path."""

    id: str
    root_class: str
    source_class: str
    target_class: str
    source_path: str
    target_path: str
    member: str
    kind: str
    file: str
    line: int


@dataclass(frozen=True, slots=True)
class ApiMethod:
    """Connect an effective public method variant to its user-facing path."""

    id: str
    root_class: str
    resource_class: str
    defined_in: str
    client_path: str
    method_name: str
    method_id: str
    method_kind: str
    inherited: bool


@dataclass(frozen=True, slots=True)
class OutputField:
    """Describe a returned model field and the class that declares it."""

    model_id: str
    class_id: str
    name: str
    annotation: str
    default: str
    requiredness: str
    inherited: bool


@dataclass(frozen=True, slots=True)
class CallableOutput:
    """Report expected return structure as static evidence with explicit gaps."""

    callable_id: str
    annotation: str
    model_ids: tuple[str, ...]
    fields: tuple[OutputField, ...]
    status: str
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Inventory:
    """Contain source evidence and bounded, statically resolved public paths."""

    source: SourceInventory
    api_methods: tuple[ApiMethod, ...]
    resource_graph: tuple[GraphEdge, ...]
    issues: tuple[ScanIssue, ...] = ()
    callable_outputs: tuple[CallableOutput, ...] = ()
