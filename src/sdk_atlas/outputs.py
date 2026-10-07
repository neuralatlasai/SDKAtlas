"""Infer bounded return-model fields from source declarations, never SDK calls."""

from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from sdk_atlas.models import (
    CallableOutput,
    ClassField,
    ImportBinding,
    OutputField,
    ScanIssue,
    SourceInventory,
)


@dataclass(slots=True)
class _AliasNode:
    children: dict[str, _AliasNode] = dataclass_field(default_factory=dict)
    binding: tuple[str, ImportBinding] | None = None


class _Aliases:
    """Locate source alias expressions through an indexed import prefix trie."""

    def __init__(self, source: SourceInventory) -> None:
        self.imports = {
            module.name: {binding.name: binding for binding in module.imports}
            for module in source.modules
        }
        self.classes = frozenset(record.id for record in source.classes)
        self.root = _AliasNode()
        for module in source.modules:
            for binding in module.imports:
                node = self.root
                for part in (*module.name.split("."), binding.name):
                    if part not in node.children:
                        node.children[part] = _AliasNode()
                    node = node.children[part]
                node.binding = module.name, binding

    def find(self, module: str, reference: str) -> tuple[str, str] | None:
        scope, current = module, reference
        visited: set[tuple[str, str]] = set()
        for _ in range(32):
            key = scope, current
            if key in visited or f"{scope}.{current}" in self.classes:
                return None
            visited.add(key)
            if scope:
                head, _, suffix = current.partition(".")
                binding = self.imports.get(scope, {}).get(head)
                owner = scope
                if binding is None:
                    scope = ""
                    continue
            else:
                parts = current.split(".")
                node = self.root
                applicable: tuple[str, ImportBinding, int] | None = None
                for index, part in enumerate(parts):
                    if part not in node.children:
                        break
                    node = node.children[part]
                    if node.binding is not None:
                        applicable = (*node.binding, index + 1)
                if applicable is None:
                    return None
                owner, binding, end = applicable
                suffix = ".".join(parts[end:])
            if binding.kind == "alias" and not suffix and binding.expression:
                return owner, binding.expression
            current = f"{binding.target}.{suffix}" if suffix else binding.target
            scope = owner if binding.kind == "alias" else ""
        return None


def _references(
    module: str,
    annotation: str,
    limit: int,
    classify: Callable[[str, str], tuple[str, str]],
    alias: Callable[[str, str], tuple[str, str] | None],
) -> tuple[tuple[tuple[str, str], ...], bool, bool]:
    if not annotation:
        return (), False, False
    try:
        tree = ast.parse(annotation, mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return ((module, annotation),), False, False
    pending: list[tuple[ast.AST, str, int, frozenset[tuple[str, str]]]] = [
        (tree, module, 0, frozenset())
    ]
    references: dict[tuple[str, str], None] = {}
    visited = 0
    unknown = False
    while pending:
        node, scope, depth, active = pending.pop()
        if visited >= limit or depth > 32:
            return tuple(references), True, unknown
        visited += 1
        if isinstance(node, (ast.Name, ast.Attribute)):
            reference = ast.unparse(node)
            expansion = alias(scope, reference)
            if expansion is None:
                references[scope, reference] = None
                continue
            if expansion in active:
                unknown = True
                references[scope, reference] = None
                continue
            owner, expression = expansion
            try:
                parsed = ast.parse(expression, mode="eval").body
            except (SyntaxError, ValueError, RecursionError):
                unknown = True
            else:
                pending.append((parsed, owner, depth + 1, active | {expansion}))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval").body
            except (SyntaxError, ValueError, RecursionError):
                references[scope, node.value] = None
            else:
                pending.append((parsed, scope, depth + 1, active))
        elif isinstance(node, ast.Subscript):
            pending.append((node.value, scope, depth + 1, active))
            items: Sequence[ast.expr] = (
                node.slice.elts if isinstance(node.slice, ast.Tuple) else (node.slice,)
            )
            _, outer = classify(scope, ast.unparse(node.value))
            if outer in {
                "typing.Literal",
                "typing_extensions.Literal",
                "typing.Callable",
                "collections.abc.Callable",
                "typing.TypeGuard",
                "typing.TypeIs",
                "typing_extensions.TypeGuard",
                "typing_extensions.TypeIs",
            }:
                # Literal arguments are values; Callable arguments describe the
                # returned function, not fields of this callable's response.
                items = ()
            elif outer in {"typing.Annotated", "typing_extensions.Annotated"}:
                items = items[:1]
            pending.extend((item, scope, depth + 1, active) for item in reversed(items))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            pending.extend(
                (
                    (node.right, scope, depth + 1, active),
                    (node.left, scope, depth + 1, active),
                )
            )
        elif isinstance(node, (ast.Tuple, ast.List)):
            pending.extend(
                (item, scope, depth + 1, active) for item in reversed(node.elts)
            )
        elif isinstance(node, ast.Starred):
            pending.append((node.value, scope, depth + 1, active))
        elif not (
            isinstance(node, ast.Constant)
            and (node.value is None or node.value is Ellipsis)
        ):
            # Calls and runtime expressions cannot establish a static shape.
            unknown = True
    return tuple(references), False, unknown


def resolve_outputs(
    source: SourceInventory,
    *,
    resolve_class: Callable[[str, str], str],
    ancestors: Mapping[str, tuple[str, ...]],
    callable_ids: frozenset[str],
    max_records: int,
    classify_reference: Callable[[str, str], tuple[str, str]],
) -> tuple[tuple[CallableOutput, ...], tuple[ScanIssue, ...]]:
    """Resolve return annotations and declared fields with shared bounded caches.

    Args:
        source: Target and explicitly included support declarations.
        resolve_class: Indexed, import-safe class symbol resolver.
        ancestors: Statically resolved C3 ancestry per discovered class.
        callable_ids: Method definitions selected independently of graph roots.
        max_records: Positive field-expansion and per-annotation traversal limit.
        classify_reference: Return canonical resolution kind and reference.

    Returns:
        One output record per selected method or supplied function, plus diagnostics.
        Fields describe declared source structure, rather than observed values.
        Unannotated, external, native, and incomplete shapes stay explicit.

    Raises:
        ValueError: The configured bound is not positive.

    Preprocessing is O(source fields + methods + dynamic members). Effective
    fields are expanded once per returned model and bounded by max_records;
    annotations are cached by module/text. Repeated API paths reuse definitions.
    No defaults, annotations, or target functions are evaluated.
    """
    if max_records < 1:
        raise ValueError("output max_records must be positive")
    aliases = _Aliases(source)
    classes = {record.id: record for record in source.classes}
    fields: dict[str, dict[str, ClassField]] = defaultdict(dict)
    for field in source.class_fields:
        fields[field.class_id][field.name] = field
    for member in source.dynamic_members:
        fields[member.class_id].setdefault(
            member.name,
            ClassField(
                member.id,
                member.class_id,
                member.name,
                member.annotation,
                member.expression,
                "unknown",
                member.file,
                member.line,
            ),
        )
    for method in source.methods:
        if method.is_property:
            fields[method.class_id].setdefault(
                method.name,
                ClassField(
                    method.id,
                    method.class_id,
                    method.name,
                    method.return_annotation,
                    "",
                    "unknown",
                    method.file,
                    method.line,
                ),
            )
    model_cache: dict[str, tuple[tuple[OutputField, ...], tuple[str, ...]]] = {}
    projection_cache: dict[
        tuple[str, ...], tuple[tuple[OutputField, ...], tuple[str, ...]]
    ] = {}
    annotation_cache: dict[
        tuple[str, str], tuple[tuple[str, ...], tuple[str, ...], bool]
    ] = {}
    issues: list[ScanIssue] = []
    expanded = 0
    projected_count = 0

    def model_fields(model_id: str) -> tuple[tuple[OutputField, ...], tuple[str, ...]]:
        nonlocal expanded
        if model_id in model_cache:
            return model_cache[model_id]
        found: list[OutputField] = []
        missing: dict[str, None] = {}
        names: set[str] = set()
        ancestry = ancestors.get(model_id, (model_id,))
        included = frozenset(ancestry)
        for owner in ancestry:
            record = classes[owner]
            for base in record.bases:
                kind, canonical = classify_reference(record.module, base)
                if kind not in {"local", "builtin"} or (
                    kind == "local" and canonical not in included
                ):
                    missing[canonical] = None
            for name, field in fields.get(owner, {}).items():
                if expanded == max_records:
                    missing["<output field limit>"] = None
                    if not issues:
                        issues.append(
                            ScanIssue(
                                "output_field_limit",
                                f"Response field expansion exceeds {max_records} "
                                "records.",
                            )
                        )
                    model_cache[model_id] = (tuple(found), tuple(missing))
                    return model_cache[model_id]
                expanded += 1
                if name in names:
                    continue
                names.add(name)
                if field.kind != "class_variable":
                    found.append(
                        OutputField(
                            model_id,
                            owner,
                            name,
                            field.annotation,
                            field.default,
                            field.requiredness,
                            owner != model_id,
                        )
                    )
        model_cache[model_id] = (tuple(found), tuple(missing))
        return model_cache[model_id]

    def annotated_models(
        module: str, annotation: str
    ) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
        key = (module, annotation)
        if key in annotation_cache:
            return annotation_cache[key]
        refs, limited, unknown = _references(
            module,
            annotation,
            max_records,
            classify_reference,
            aliases.find,
        )
        models: dict[str, None] = {}
        missing: dict[str, None] = {}
        for scope, reference in refs:
            target = resolve_class(scope, reference)
            if target:
                models[target] = None
                continue
            kind, canonical = classify_reference(scope, reference)
            if kind == "builtin":
                continue
            # These are Python annotation facilities, not SDK/resource names.
            # Any explicitly means that a response shape is not specified.
            if canonical.startswith(
                ("typing.", "typing_extensions.", "collections.abc.")
            ):
                unknown |= canonical.rsplit(".", 1)[-1] in {
                    "Any",
                    "Self",
                    "TypeVar",
                    "ParamSpec",
                    "TypeVarTuple",
                }
                continue
            missing[canonical] = None
        if limited:
            missing["<annotation traversal limit>"] = None
        annotation_cache[key] = (tuple(models), tuple(missing), unknown)
        return annotation_cache[key]

    def projection(
        model_ids: tuple[str, ...],
    ) -> tuple[tuple[OutputField, ...], tuple[str, ...]]:
        nonlocal projected_count
        if model_ids in projection_cache:
            return projection_cache[model_ids]
        if len(model_ids) == 1:
            # Sharing the immutable tuple prevents O(callables * fields) retained
            # references when many definitions return the same response model.
            result = model_fields(model_ids[0])
        else:
            combined: list[OutputField] = []
            missing: dict[str, None] = {}
            for model_id in model_ids:
                effective, gaps = model_fields(model_id)
                missing.update(dict.fromkeys(gaps))
                remaining = max_records - projected_count
                if len(effective) > remaining:
                    missing["<output projection limit>"] = None
                    if not any(
                        issue.kind == "output_projection_limit" for issue in issues
                    ):
                        issues.append(
                            ScanIssue(
                                "output_projection_limit",
                                f"Response union projections exceed {max_records} "
                                "field references.",
                            )
                        )
                    combined.extend(effective[:remaining])
                    projected_count += remaining
                    break
                combined.extend(effective)
                projected_count += len(effective)
            result = tuple(combined), tuple(missing)
        projection_cache[model_ids] = result
        return result

    records: list[CallableOutput] = []

    def add(
        callable_id: str, module: str, annotation: str, returns: tuple[str, ...] = ()
    ) -> None:
        if annotation:
            model_ids, absent, unknown = annotated_models(module, annotation)
        else:
            model_ids = tuple(
                dict.fromkeys(
                    target
                    for reference in returns
                    if (target := resolve_class(module, reference))
                )
            )
            absent = ()
            unknown = False
        projected, gaps = projection(model_ids)
        unresolved = dict.fromkeys(absent)
        unresolved.update(dict.fromkeys(gaps))
        status = (
            "partial"
            if model_ids and (unresolved or unknown)
            else "inferred"
            if model_ids and not annotation
            else "resolved"
            if model_ids
            else "unannotated"
            if not annotation
            else "unresolved"
            if unresolved
            else "unknown"
            if unknown
            else "type_only"
        )
        records.append(
            CallableOutput(
                callable_id,
                annotation,
                model_ids,
                projected,
                status,
                tuple(unresolved),
            )
        )

    for method in source.methods:
        if method.id in callable_ids:
            add(
                method.id,
                classes[method.class_id].module,
                method.return_annotation,
                method.returns,
            )
    for function in source.functions:
        add(function.id, function.module, function.return_annotation)
    return tuple(records), tuple(issues)
