"""Extract bounded, deterministic SDK evidence from Python syntax trees.

Each source node is visited a constant number of times. Parsing and extraction
are O(source bytes + AST nodes + emitted records), with O(records + one module
AST) working storage. Sorting source paths costs O(f log f), with comparisons
bounded by path length. Target modules are never imported or evaluated.
"""

from __future__ import annotations

import ast
import io
import os
import tokenize
from dataclasses import replace
from pathlib import Path

from sdk_atlas.models import (
    ClassField,
    ClassRecord,
    DynamicMember,
    FunctionRecord,
    ImportBinding,
    MethodRecord,
    ModuleMember,
    ModuleRecord,
    Parameter,
    ScanIssue,
    SourceInventory,
    TransportEvidence,
)

_ROUTE_KEYWORDS = frozenset({"url", "path", "route", "uri", "endpoint"})
_OPERATIONS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_PROPERTIES = frozenset({"property", "cached_property", "classproperty"})


def _read_source(path: Path, relative: str, limit: int) -> bytes | ScanIssue:
    """Bound allocation by actual file size and detect concurrent source changes.

    BufferedReader.read(limit + 1) allocates for the configured maximum even for
    tiny files. Sizing against the opened file avoids O(files * byte limit)
    allocation churn while preserving the hard per-file input boundary. The
    extra byte and final metadata check detect growth/shrink during the read;
    these checks do not provide an atomic snapshot against external writers.
    """
    with path.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if before.st_size > limit:
            return ScanIssue(
                "source_size_limit", f"source exceeds {limit} bytes", relative
            )
        data = handle.read(before.st_size + 1)
        after = os.fstat(handle.fileno())
    if len(data) != before.st_size or (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ):
        return ScanIssue(
            "source_changed",
            "Source changed while being read; rerun the scan.",
            relative,
        )
    return data


def _ast_size_exceeded(tree: ast.AST, limit: int) -> bool:
    """Count AST references in one iterative pass, stopping at the input bound.

    A LIFO frontier avoids the generator/deque layers of ast.walk followed by
    enumerate/any. Both traverse O(nodes); the explicit stack retains repeated
    singleton context references, matching ast.walk's exact node-budget semantics.
    """
    pending = [tree]
    count = 0
    while pending:
        node = pending.pop()
        count += 1
        if count > limit:
            return True
        for name in node._fields:
            value = getattr(node, name)
            if isinstance(value, ast.AST):
                pending.append(value)
            elif isinstance(value, list):
                pending.extend(item for item in value if isinstance(item, ast.AST))
    return False


def _text(node: ast.AST | None) -> str:
    if node is None:
        return ""
    # Annotations with quoted forward references must resolve like plain names.
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ast.unparse(node)


def _expression(node: ast.AST | None) -> str:
    return "" if node is None else ast.unparse(node)


def _symbol(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _symbol(node.value)
        return f"{parent}.{node.attr}" if parent else ""
    if isinstance(node, ast.Subscript):
        return _symbol(node.value)
    return ""


def _annotation_refs(node: ast.AST | None) -> tuple[str, ...]:
    if node is None:
        return ()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return ()
    refs: list[str] = []
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, ast.BinOp) and isinstance(current.op, ast.BitOr):
            pending.extend((current.right, current.left))
        elif isinstance(current, ast.Subscript) and _symbol(current.value).rsplit(
            ".", 1
        )[-1] in {"Optional", "Union", "Annotated"}:
            items = (
                list(current.slice.elts)
                if isinstance(current.slice, ast.Tuple)
                else [current.slice]
            )
            pending.extend(
                reversed(
                    items[:1] if _symbol(current.value).endswith("Annotated") else items
                )
            )
        elif symbol := _symbol(current):
            refs.append(symbol)
    return tuple(dict.fromkeys(refs))


def _parameters(args: ast.arguments, receiver: str = "") -> tuple[Parameter, ...]:
    result: list[Parameter] = []
    positional = [*args.posonlyargs, *args.args]
    default_start = len(positional) - len(args.defaults)
    for index, arg in enumerate(positional):
        default = (
            args.defaults[index - default_start] if index >= default_start else None
        )
        result.append(
            Parameter(
                arg.arg,
                "POSITIONAL_ONLY"
                if index < len(args.posonlyargs)
                else "POSITIONAL_OR_KEYWORD",
                _text(arg.annotation),
                _expression(default),
                default is None,
                bool(receiver) and index == 0 and arg.arg == receiver,
            )
        )
    if args.vararg:
        result.append(
            Parameter(
                args.vararg.arg,
                "VAR_POSITIONAL",
                _text(args.vararg.annotation),
                required=False,
            )
        )
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        result.append(
            Parameter(
                arg.arg,
                "KEYWORD_ONLY",
                _text(arg.annotation),
                _expression(default),
                default is None,
            )
        )
    if args.kwarg:
        result.append(
            Parameter(
                args.kwarg.arg,
                "VAR_KEYWORD",
                _text(args.kwarg.annotation),
                required=False,
            )
        )
    return tuple(result)


def _route(node: ast.AST) -> tuple[str, str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, "literal"
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                # Keep conversion and formatting evidence in the expression,
                # while presenting a readable route template.
                parts.append("{" + _expression(value.value) + "}")
        return "".join(parts), "formatted"
    return _expression(node), "expression"


def _looks_like_route(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.startswith(("/", "https://", "http://"))
    if isinstance(node, ast.JoinedStr) and node.values:
        return _looks_like_route(node.values[0])
    if isinstance(node, ast.BinOp):
        return _looks_like_route(node.left)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "format" and _looks_like_route(node.func.value)
    return False


class _MethodBody(ast.NodeVisitor):
    """Collect evidence in a single method scope, excluding nested definitions."""

    def __init__(
        self, class_id: str, method_id: str, module: str, file: str, receiver: str = ""
    ) -> None:
        self.class_id = class_id
        self.method_id = method_id
        self.module = module
        self.file = file
        self.receiver = receiver
        self.transports: list[TransportEvidence] = []
        self.dynamic: list[DynamicMember] = []
        self.returns: list[str] = []
        self.delegates: list[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Exclude a nested function's independent lexical scope."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Exclude a nested coroutine's independent lexical scope."""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Exclude a nested class's independent lexical scope."""

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Exclude a deferred lambda body from direct transport evidence."""

    def visit_Return(self, node: ast.Return) -> None:
        """Record returned constructors and aliases as graph candidates."""
        if isinstance(node.value, ast.Call):
            candidate = _symbol(node.value.func)
        elif node.value is not None:
            candidate = _symbol(node.value)
        else:
            candidate = ""
        if candidate:
            self.returns.append(candidate)
        self.generic_visit(node)

    def _assignment(
        self,
        target: ast.AST,
        value: ast.AST | None,
        annotation: ast.AST | None,
        line: int,
    ) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(target.elts) == len(
                value.elts
            ):
                for item, assigned in zip(target.elts, value.elts, strict=True):
                    self._assignment(item, assigned, None, line)
            return
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and self.receiver
            and target.value.id == self.receiver
            and not target.attr.startswith("_")
        ):
            return
        candidates = list(_annotation_refs(annotation))
        if isinstance(value, ast.Call):
            candidates.append(_symbol(value.func))
        elif value is not None:
            candidates.append(_symbol(value))
        self.dynamic.append(
            DynamicMember(
                f"{self.method_id}:{target.attr}:{line}:{len(self.dynamic)}",
                self.class_id,
                target.attr,
                _expression(value),
                _text(annotation),
                tuple(dict.fromkeys(ref for ref in candidates if ref)),
                self.file,
                line,
            )
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        """Capture public instance assignments, including tuple unpacking."""
        for target in node.targets:
            self._assignment(target, node.value, None, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Capture annotated public instance assignments."""
        self._assignment(node.target, node.value, node.annotation, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Record public delegation and path-bearing calls with HTTP hints."""
        symbol = _symbol(node.func)
        parts = symbol.rsplit(".", 1)
        leaf = parts[-1].lower().lstrip("_")
        operation = leaf if leaf in _OPERATIONS else "unknown"
        if (
            self.receiver
            and symbol.startswith(f"{self.receiver}.")
            and symbol.count(".") == 1
            and not parts[-1].startswith("_")
        ):
            self.delegates.append(parts[-1])
        candidates: list[ast.AST] = [
            arg for arg in node.args[:1] if _looks_like_route(arg)
        ]
        for keyword in node.keywords:
            if keyword.arg in _ROUTE_KEYWORDS:
                candidates.append(keyword.value)
            elif keyword.arg == "method" and isinstance(keyword.value, ast.Constant):
                hint = str(keyword.value.value).lower()
                if hint in _OPERATIONS:
                    operation = hint
        # Generic request(verb, route) and transport symbols with variable paths
        # are recognized without an SDK resource or endpoint catalogue.
        if leaf == "request" and len(node.args) >= 2:
            first = node.args[0]
            if (
                isinstance(first, ast.Constant)
                and str(first.value).lower() in _OPERATIONS
            ):
                operation = str(first.value).lower()
                candidates.append(node.args[1])
        elif (
            leaf in _OPERATIONS
            and parts[-1].startswith("_")
            and node.args
            and not candidates
        ):
            candidates.append(node.args[0])
        seen: set[str] = set()
        for candidate in candidates:
            route, kind = _route(candidate)
            if route in seen:
                continue
            seen.add(route)
            self.transports.append(
                TransportEvidence(
                    f"{self.method_id}:transport:{len(self.transports)}",
                    self.method_id,
                    self.class_id,
                    self.module,
                    self.file,
                    node.lineno,
                    _expression(node.func),
                    operation.upper() if operation != "unknown" else operation,
                    route,
                    kind,
                )
            )
        self.generic_visit(node)


class _ModuleScanner(ast.NodeVisitor):
    def __init__(self, module: str, file: str, is_package: bool) -> None:
        self.module = module
        self.file = file
        self.is_package = is_package
        self.imports: dict[str, str] = {}
        self.binding_kinds: dict[str, str] = {}
        self.binding_expressions: dict[str, str] = {}
        self.exports: list[str] = []
        self.classes: list[ClassRecord] = []
        self.methods: list[MethodRecord] = []
        self.method_variants: dict[tuple[str, str], list[MethodRecord]] = {}
        self.dynamic: list[DynamicMember] = []
        self.functions: list[FunctionRecord] = []
        self.module_members: list[ModuleMember] = []
        self.class_fields: list[ClassField] = []
        self.scope: list[str] = []
        self.field_requirements: list[str] = []

    def _decorator_leaves(self, nodes: list[ast.expr]) -> set[str]:
        leaves: set[str] = set()
        for node in nodes:
            target = node.func if isinstance(node, ast.Call) else node
            reference = _symbol(target)
            parts = reference.split(".")
            imported = self.imports.get(parts[0], parts[0])
            resolved = ".".join([imported, *parts[1:]])
            leaves.add(resolved.rsplit(".", 1)[-1])
        return leaves

    def _typing_markers(self, node: ast.expr) -> set[str]:
        """Recognize canonical typing qualifiers, including nested wrappers."""
        markers: set[str] = set()
        current = node
        while True:
            reference = _symbol(current)
            head, _, suffix = reference.partition(".")
            resolved = self.imports.get(head, head)
            if suffix:
                resolved += "." + suffix
            if resolved.startswith(("typing.", "typing_extensions.")):
                markers.add(resolved.rsplit(".", 1)[-1])
            if not isinstance(current, ast.Subscript):
                break
            current = (
                current.slice.elts[0]
                if isinstance(current.slice, ast.Tuple) and current.slice.elts
                else current.slice
            )
        return markers

    def _member(
        self, name: str, kind: str, expression: str, annotation: str, line: int
    ) -> None:
        if not name.startswith("_") or name == "*":
            self.module_members.append(
                ModuleMember(
                    f"{self.module}.{name}:{self.file}:{line}:{len(self.module_members)}",
                    self.module,
                    name,
                    kind,
                    expression,
                    annotation,
                    self.file,
                    line,
                )
            )

    def visit_Import(self, node: ast.Import) -> None:
        if not self.scope:
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                self.imports[local_name] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
                self.binding_kinds[local_name] = "import"
                self.binding_expressions.pop(local_name, None)
                self._member(
                    alias.asname or alias.name.split(".")[0],
                    "import",
                    alias.name,
                    "",
                    node.lineno,
                )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.scope:
            return
        if node.level:
            parent = (
                self.module.split(".")
                if self.is_package
                else self.module.split(".")[:-1]
            )
            trim = node.level - 1
            prefix = parent[: len(parent) - trim] if trim <= len(parent) else []
            if node.module:
                prefix.extend(node.module.split("."))
            base = ".".join(prefix)
        else:
            base = node.module or ""
        for alias in node.names:
            if alias.name != "*":
                local_name = alias.asname or alias.name
                self.imports[local_name] = f"{base}.{alias.name}".strip(".")
                self.binding_kinds[local_name] = "import"
                self.binding_expressions.pop(local_name, None)
            self._member(
                alias.asname or alias.name,
                "wildcard_import" if alias.name == "*" else "import",
                f"{base}.{alias.name}".strip("."),
                "",
                node.lineno,
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.scope:
            if isinstance(node.value, ast.Name):
                class_id = f"{self.module}.{'.'.join(self.scope)}"
                variants = self.method_variants.get((class_id, node.value.id), ())
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id != node.value.id:
                        for index, variant in enumerate(tuple(variants)):
                            method_id = (
                                f"{class_id}.{target.id}:{self.file}:{node.lineno}:"
                                f"alias:{index}"
                            )
                            alias = replace(
                                variant,
                                id=method_id,
                                name=target.id,
                                signature=target.id
                                + variant.signature[len(variant.name) :],
                                line=node.lineno,
                                end_line=node.end_lineno or node.lineno,
                                transport=tuple(
                                    replace(
                                        evidence,
                                        id=f"{method_id}:transport:{position}",
                                        method_id=method_id,
                                    )
                                    for position, evidence in enumerate(
                                        variant.transport
                                    )
                                ),
                            )
                            self.method_variants.setdefault(
                                (class_id, target.id), []
                            ).append(alias)
                            if not target.id.startswith("_") or (
                                target.id.startswith("__") and target.id.endswith("__")
                            ):
                                self.methods.append(alias)
            return
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if isinstance(node.value, ast.Call) and self._typing_factory(
                target.id, node.value, node.lineno
            ):
                self.imports.pop(target.id, None)
                self.binding_kinds.pop(target.id, None)
                self.binding_expressions.pop(target.id, None)
                self._member(
                    target.id, "class", _expression(node.value), "", node.lineno
                )
                continue
            if target.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                self.exports.extend(
                    item.value
                    for item in node.value.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
            elif symbol := _symbol(node.value):
                if symbol != target.id or target.id not in self.imports:
                    self.imports[target.id] = symbol
                    self.binding_kinds[target.id] = "alias"
                    self.binding_expressions[target.id] = _expression(node.value)
            elif isinstance(node.value, ast.BinOp) and isinstance(
                node.value.op, ast.BitOr
            ):
                self.imports[target.id] = _expression(node.value)
                self.binding_kinds[target.id] = "alias"
                self.binding_expressions[target.id] = _expression(node.value)
            else:
                # A runtime value replaces a prior import; keeping that import
                # would invent an external base after shadowing its local name.
                self.imports.pop(target.id, None)
                self.binding_kinds.pop(target.id, None)
                self.binding_expressions.pop(target.id, None)
            self._member(
                target.id,
                "alias" if _symbol(node.value) else "constant",
                _expression(node.value),
                "",
                node.lineno,
            )

    def _typing_factory(self, name: str, node: ast.Call, line: int) -> bool:
        """Recover functional typing declarations from literal field evidence.

        Only canonical Python typing factories are recognized. No arbitrary
        constructors are called, and nonliteral field collections stay dynamic.
        """
        markers = self._typing_markers(node.func)
        if not markers & {"TypedDict", "NamedTuple"} or len(node.args) != 2:
            return False
        field_node = node.args[1]
        declared: list[tuple[str, ast.expr]] = []
        if "TypedDict" in markers and isinstance(field_node, ast.Dict):
            for key, value in zip(field_node.keys, field_node.values, strict=True):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    return False
                declared.append((key.value, value))
        elif "NamedTuple" in markers and isinstance(field_node, (ast.List, ast.Tuple)):
            for item in field_node.elts:
                if not (
                    isinstance(item, (ast.List, ast.Tuple))
                    and len(item.elts) == 2
                    and isinstance(item.elts[0], ast.Constant)
                    and isinstance(item.elts[0].value, str)
                ):
                    return False
                declared.append((item.elts[0].value, item.elts[1]))
        else:
            return False
        class_id = f"{self.module}.{name}"
        self.classes.append(
            ClassRecord(
                class_id,
                self.module,
                name,
                name,
                (_expression(node.func),),
                (),
                "",
                self.file,
                line,
            )
        )
        requiredness = "required"
        for keyword in node.keywords:
            if keyword.arg == "total":
                if isinstance(keyword.value, ast.Constant) and isinstance(
                    keyword.value.value, bool
                ):
                    requiredness = "required" if keyword.value.value else "optional"
                else:
                    requiredness = "unknown"
        for field_name, annotation in declared:
            qualifiers = self._typing_markers(annotation)
            requirement = (
                "required"
                if "Required" in qualifiers
                else "optional"
                if "NotRequired" in qualifiers
                else requiredness
            )
            self.class_fields.append(
                ClassField(
                    f"{class_id}.{field_name}:{self.file}:{line}:"
                    f"{len(self.class_fields)}",
                    class_id,
                    field_name,
                    _text(annotation),
                    "",
                    requirement,
                    self.file,
                    line,
                )
            )
        return True

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self.scope and isinstance(node.target, ast.Name):
            name = node.target.id
            if not name.startswith("_"):
                markers = self._typing_markers(node.annotation)
                requiredness = self.field_requirements[-1]
                if "NotRequired" in markers:
                    requiredness = "optional"
                elif "Required" in markers:
                    requiredness = "required"
                elif node.value is not None:
                    requiredness = (
                        "unknown"
                        if isinstance(node.value, ast.Call)
                        or (
                            isinstance(node.value, ast.Constant)
                            and node.value.value is Ellipsis
                        )
                        else "optional"
                    )
                class_id = f"{self.module}.{'.'.join(self.scope)}"
                self.class_fields.append(
                    ClassField(
                        f"{class_id}.{name}:{self.file}:{node.lineno}:"
                        f"{len(self.class_fields)}",
                        class_id,
                        name,
                        _text(node.annotation),
                        _expression(node.value),
                        requiredness,
                        self.file,
                        node.lineno,
                        "class_variable" if "ClassVar" in markers else "data",
                    )
                )
            return
        if not self.scope and isinstance(node.target, ast.Name):
            self._member(
                node.target.id,
                "alias"
                if node.value is not None and _symbol(node.value)
                else "variable",
                _expression(node.value),
                _text(node.annotation),
                node.lineno,
            )
            if node.value is not None and (symbol := _symbol(node.value)):
                if symbol != node.target.id or node.target.id not in self.imports:
                    self.imports[node.target.id] = symbol
                    self.binding_kinds[node.target.id] = "alias"
                    self.binding_expressions[node.target.id] = _expression(node.value)
            elif isinstance(node.value, ast.BinOp) and isinstance(
                node.value.op, ast.BitOr
            ):
                self.imports[node.target.id] = _expression(node.value)
                self.binding_kinds[node.target.id] = "alias"
                self.binding_expressions[node.target.id] = _expression(node.value)
            elif node.value is not None:
                self.imports.pop(node.target.id, None)
                self.binding_kinds.pop(node.target.id, None)
                self.binding_expressions.pop(node.target.id, None)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.scope:
            self._method(node)
        else:
            self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.scope:
            self._method(node)
        else:
            self._function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if not self.scope:
            self._member(node.name, "class", node.name, "", node.lineno)
        self.scope.append(node.name)
        optional_fields = any(
            "TypedDict" in self._typing_markers(base) for base in node.bases
        ) and any(
            keyword.arg == "total"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
            for keyword in node.keywords
        )
        self.field_requirements.append("optional" if optional_fields else "required")
        qualname = ".".join(self.scope)
        self.classes.append(
            ClassRecord(
                f"{self.module}.{qualname}",
                self.module,
                node.name,
                qualname,
                tuple(_expression(base) for base in node.bases),
                tuple(_expression(item) for item in node.decorator_list),
                ast.get_docstring(node) or "",
                self.file,
                node.lineno,
            )
        )
        for statement in node.body:
            self.visit(statement)
        self.scope.pop()
        self.field_requirements.pop()

    def _method(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        class_id = f"{self.module}.{'.'.join(self.scope)}"
        method_id = f"{class_id}.{node.name}:{self.file}:{node.lineno}"
        positional = [*node.args.posonlyargs, *node.args.args]
        leaves = self._decorator_leaves(node.decorator_list)
        binding = (
            "static"
            if "staticmethod" in leaves
            else "class"
            if "classmethod" in leaves
            else "instance"
        )
        receiver = positional[0].arg if positional and binding != "static" else ""
        body = _MethodBody(
            class_id,
            method_id,
            self.module,
            self.file,
            receiver,
        )
        for statement in node.body:
            body.visit(statement)
        self.dynamic.extend(body.dynamic)
        decorators = tuple(_expression(item) for item in node.decorator_list)
        annotation = _text(node.returns)
        signature = f"{node.name}({ast.unparse(node.args)})"
        if annotation:
            signature += f" -> {annotation}"
        method = MethodRecord(
            method_id,
            class_id,
            node.name,
            signature,
            annotation,
            _parameters(node.args, receiver),
            decorators,
            ast.get_docstring(node) or "",
            self.file,
            node.lineno,
            node.end_lineno or node.lineno,
            isinstance(node, ast.AsyncFunctionDef),
            bool(leaves & (_PROPERTIES | {"getter", "setter", "deleter"})),
            "overload" in leaves,
            tuple(dict.fromkeys(body.returns)),
            tuple(dict.fromkeys(body.delegates)),
            tuple(body.transports),
            binding,
        )
        self.method_variants.setdefault((class_id, node.name), []).append(method)
        if not node.name.startswith("_") or (
            node.name.startswith("__") and node.name.endswith("__")
        ):
            self.methods.append(method)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name.startswith("_") and not (
            node.name.startswith("__") and node.name.endswith("__")
        ):
            return
        function_id = f"{self.module}.{node.name}:{self.file}:{node.lineno}"
        body = _MethodBody("", function_id, self.module, self.file)
        for statement in node.body:
            body.visit(statement)
        decorators = tuple(_expression(item) for item in node.decorator_list)
        annotation = _text(node.returns)
        signature = f"{node.name}({ast.unparse(node.args)})"
        if annotation:
            signature += f" -> {annotation}"
        self.functions.append(
            FunctionRecord(
                function_id,
                self.module,
                node.name,
                signature,
                annotation,
                _parameters(node.args),
                decorators,
                ast.get_docstring(node) or "",
                self.file,
                node.lineno,
                node.end_lineno or node.lineno,
                isinstance(node, ast.AsyncFunctionDef),
                "overload" in self._decorator_leaves(node.decorator_list),
                tuple(body.transports),
            )
        )
        self._member(node.name, "function", node.name, annotation, node.lineno)


def scan_source(
    root: Path,
    package: str,
    *,
    version: str = "unknown",
    max_files: int = 100_000,
    max_source_bytes: int = 8 * 1024 * 1024,
    max_ast_nodes: int = 500_000,
) -> SourceInventory:
    """Read a package directory and return immutable static evidence.

    Args:
        root: Package directory, Python/stub module, or native extension module.
        package: Absolute dotted import name assigned to the root.
        version: Optional distribution version obtained without target imports.
        max_files: Positive upper bound on source files considered.
        max_source_bytes: Positive per-file input limit, checked before parsing.
        max_ast_nodes: Positive per-file syntax-tree size limit.

    Returns:
        Deterministically ordered records and diagnostics for skipped modules.

    Raises:
        ValueError: Invalid package name, directory, or nonpositive limits.
        OSError: The package directory cannot be enumerated.

    Files are read without executing code. Symlinks are skipped. Syntax,
    encoding, size, and individual read failures become explicit scan issues.
    """
    if not package or any(not part.isidentifier() for part in package.split(".")):
        raise ValueError("package must be an absolute dotted Python import name")
    if min(max_files, max_source_bytes, max_ast_nodes) <= 0:
        raise ValueError("scan limits must be positive")
    root = root.resolve(strict=True)
    single_module = root.is_file() and root.suffix in {".py", ".pyi", ".pyd", ".so"}
    if not root.is_dir() and not single_module:
        raise ValueError(f"package root must be a directory or Python module: {root}")
    paths: list[Path] = []
    issues: list[ScanIssue] = []

    def walk_error(error: OSError) -> None:
        raise error

    walker = (
        () if single_module else os.walk(root, followlinks=False, onerror=walk_error)
    )
    for directory, dirs, files in walker:
        dirs[:] = sorted(
            name
            for name in dirs
            if name != "__pycache__" and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix not in {".py", ".pyi", ".pyd", ".so"} or path.is_symlink():
                continue
            if len(paths) >= max_files:
                raise ValueError(f"source file limit exceeded ({max_files})")
            paths.append(path)
    if single_module:
        paths.append(root)
    module_index: dict[str, ModuleRecord] = {}
    class_index: dict[str, ClassRecord] = {}
    methods: list[MethodRecord] = []
    dynamic: list[DynamicMember] = []
    functions: list[FunctionRecord] = []
    module_members: list[ModuleMember] = []
    class_fields: list[ClassField] = []
    for path in sorted(paths):
        relative = path.name if single_module else path.relative_to(root).as_posix()
        normalized_name = path.name.split(".")[0]
        is_package = normalized_name == "__init__"
        parts = (
            [] if single_module else list(path.relative_to(root).with_suffix("").parts)
        )
        if parts and path.suffix in {".pyd", ".so"}:
            parts[-1] = parts[-1].split(".")[0]
        if is_package and parts:
            parts.pop()
        module = ".".join([package, *parts])
        if path.suffix in {".pyd", ".so"}:
            previous = module_index.get(module)
            module_index[module] = ModuleRecord(
                module,
                previous.file if previous else relative,
                previous.is_package if previous else is_package,
                previous.imports if previous else (),
                previous.exports if previous else (),
                f"{previous.source_kind}+native" if previous else "native",
            )
            issues.append(
                ScanIssue(
                    "native_module",
                    "Compiled module recorded without execution; "
                    "signatures require readable source or stubs.",
                    relative,
                )
            )
            continue
        try:
            data = _read_source(path, relative, max_source_bytes)
            if isinstance(data, ScanIssue):
                issues.append(data)
                continue
            encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
            tree = ast.parse(data.decode(encoding), filename=relative)
            if _ast_size_exceeded(tree, max_ast_nodes):
                issues.append(
                    ScanIssue(
                        "ast_size_limit",
                        f"source exceeds {max_ast_nodes} AST nodes",
                        relative,
                    )
                )
                continue
            visitor = _ModuleScanner(module, relative, is_package)
            visitor.visit(tree)
        except (
            OSError,
            UnicodeError,
            LookupError,
            SyntaxError,
            RecursionError,
        ) as error:
            issues.append(
                ScanIssue(
                    "source_error",
                    str(error),
                    relative,
                    getattr(error, "lineno", 0) or 0,
                )
            )
            continue
        previous = module_index.get(module)
        imports = (
            {binding.name: binding for binding in previous.imports} if previous else {}
        )
        imports.update(
            {
                name: ImportBinding(
                    name,
                    target,
                    visitor.binding_kinds[name],
                    visitor.binding_expressions.get(name, ""),
                )
                for name, target in visitor.imports.items()
            }
        )
        kind = "stub" if path.suffix == ".pyi" else "python"
        module_index[module] = ModuleRecord(
            module,
            previous.file if previous else relative,
            is_package,
            tuple(imports.values()),
            tuple(
                dict.fromkeys(
                    [*(previous.exports if previous else ()), *visitor.exports]
                )
            ),
            f"{previous.source_kind}+{kind}" if previous else kind,
        )
        for record in visitor.classes:
            class_index.setdefault(record.id, record)
        methods.extend(visitor.methods)
        dynamic.extend(visitor.dynamic)
        functions.extend(visitor.functions)
        module_members.extend(visitor.module_members)
        class_fields.extend(visitor.class_fields)
    return SourceInventory(
        package,
        str(root),
        tuple(module_index.values()),
        tuple(class_index.values()),
        tuple(methods),
        tuple(dynamic),
        tuple(issues),
        version,
        tuple(functions),
        tuple(module_members),
        class_fields=tuple(class_fields),
    )
