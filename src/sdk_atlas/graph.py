"""Resolve bounded inheritance and resource paths without importing SDK code."""

from __future__ import annotations

import ast
import builtins
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Literal

from sdk_atlas.models import (
    ApiMethod,
    DynamicMember,
    GraphEdge,
    ImportBinding,
    Inventory,
    MethodRecord,
    ScanIssue,
    SourceInventory,
)
from sdk_atlas.outputs import resolve_outputs


@dataclass(frozen=True, slots=True)
class _Transition:
    """Carry static evidence for a public attribute's resource target."""

    member: str
    target: str
    kind: str
    file: str
    line: int


@dataclass(slots=True)
class _ImportNode:
    """Index module components without repeatedly slicing qualified names."""

    children: dict[str, _ImportNode] = field(default_factory=dict)
    bindings: dict[str, ImportBinding] = field(default_factory=dict)
    module: str = ""
    in_scope: bool = False


@dataclass(frozen=True, slots=True)
class _Resolution:
    """Distinguish known classes from proven external and incomplete references."""

    kind: Literal["local", "builtin", "external", "unresolved", "cycle", "limit"]
    reference: str


class _Resolver:
    """Own per-scan indexes, bounded caches, and incomplete-evidence diagnostics."""

    def __init__(self, source: SourceInventory, max_depth: int, budget: int) -> None:
        self.source = source
        self.max_depth = max_depth
        self.budget = budget
        self.classes = {record.id: record for record in source.classes}
        self.modules = {record.name: record for record in source.modules}
        self.imports = {
            module.name: {binding.name: binding for binding in module.imports}
            for module in source.modules
        }
        self.import_root = _ImportNode()
        for module, bindings in self.imports.items():
            node = self.import_root
            for part in module.split("."):
                if part not in node.children:
                    node.children[part] = _ImportNode()
                node = node.children[part]
            node.bindings = bindings
            node.module = module
        for package in (
            source.package,
            *(reference.package for reference in source.support_sources),
        ):
            node = self.import_root
            for part in package.split("."):
                if part not in node.children:
                    node.children[part] = _ImportNode()
                node = node.children[part]
            node.in_scope = True
        self.builtin_types = {
            name: value
            for name, value in vars(builtins).items()
            if isinstance(value, type)
        }
        self.runtime_names = {
            (member.module, member.name)
            for member in source.module_members
            if member.kind != "variable" or member.expression
        }
        self.runtime_names.update(
            (function.module, function.name) for function in source.functions
        )
        self.methods: dict[str, dict[str, list[MethodRecord]]] = defaultdict(dict)
        self.members: dict[str, dict[str, list[DynamicMember]]] = defaultdict(dict)
        for method in source.methods:
            self.methods[method.class_id].setdefault(method.name, []).append(method)
        for member in source.dynamic_members:
            self.members[member.class_id].setdefault(member.name, []).append(member)
        self.descriptors = {
            record.id: tuple(
                dict.fromkeys(
                    (
                        *self.members.get(record.id, {}),
                        *(
                            name
                            for name, variants in self.methods.get(
                                record.id, {}
                            ).items()
                            if any(method.is_property for method in variants)
                        ),
                    )
                )
            )
            for record in source.classes
        }
        self.resolutions: dict[tuple[str, str], _Resolution] = {}
        self.mros: dict[str, tuple[str, ...]] = {}
        self.edges: dict[str, tuple[_Transition, ...]] = {}
        self.effective_methods: dict[str, tuple[MethodRecord, ...]] = {}
        self.issues: list[ScanIssue] = []
        self.issue_keys: set[tuple[str, str, str, int]] = set()
        self.expansions = 0
        self.surface_expansions = 0

    def issue(self, kind: str, message: str, file: str = "", line: int = 0) -> None:
        """Preserve one diagnostic for each distinct incomplete static fact."""
        key = (kind, message, file, line)
        if key not in self.issue_keys:
            self.issue_keys.add(key)
            self.issues.append(ScanIssue(kind, message, file, line))

    def symbol(self, module: str, reference: str) -> str:
        """Return a discovered class ID without inventing external class records."""
        result = self.resolution(module, reference)
        return result.reference if result.kind == "local" else ""

    def classify(self, module: str, reference: str) -> tuple[str, str]:
        """Expose canonical reference evidence without leaking internal records."""
        result = self.resolution(module, reference)
        return result.kind, result.reference

    def in_scope(self, reference: str) -> bool:
        """Check included source namespaces in O(qualified reference length)."""
        node = self.import_root
        for part in reference.split("."):
            if part not in node.children:
                return False
            node = node.children[part]
            if node.in_scope:
                return True
        return False

    def qualified_binding(
        self, reference: str
    ) -> tuple[str, ImportBinding, str] | None:
        """Find the longest module-prefix binding without repeated prefix joins."""
        parts = reference.split(".")
        node = self.import_root
        applicable: tuple[str, ImportBinding, int] | None = None
        for index, part in enumerate(parts[:-1]):
            if part not in node.children:
                break
            node = node.children[part]
            binding = node.bindings.get(parts[index + 1])
            if binding is not None:
                applicable = (node.module, binding, index + 2)
        if applicable is None:
            return None
        module, binding, suffix_index = applicable
        return module, binding, ".".join(parts[suffix_index:])

    def resolution(self, module: str, reference: str) -> _Resolution:
        """Resolve aliases with lexical scope and explicit import provenance.

        Only actual imports establish an external boundary. Assignment aliases
        keep their defining module's scope, so Alias = Ghost.Missing remains
        unresolved when Ghost is unbound. Local bindings shadow builtin names.
        """
        key = (module, reference)
        if key in self.resolutions:
            return self.resolutions[key]
        reference = _base_reference(reference)
        current = reference
        scope = module
        imported = False
        expanded: set[tuple[str, str]] = set()
        while True:
            candidate = f"{scope}.{current}" if scope else current
            if candidate in self.classes:
                result = _Resolution("local", candidate)
                break
            binding_owner = scope
            suffix = ""
            if scope:
                head, _, suffix = current.partition(".")
                binding = self.imports.get(scope, {}).get(head)
                if binding is None:
                    if current in self.classes:
                        result = _Resolution("local", current)
                    elif (
                        not suffix
                        and head in self.builtin_types
                        and (scope, head) not in self.runtime_names
                    ):
                        result = _Resolution("builtin", f"builtins.{head}")
                    elif self.in_scope(current):
                        scope = ""
                        continue
                    else:
                        result = _Resolution("unresolved", candidate)
                    break
            else:
                applicable = self.qualified_binding(current)
                if applicable is None:
                    builtin_name = current.removeprefix("builtins.")
                    if (
                        imported
                        and current.startswith("builtins.")
                        and builtin_name in self.builtin_types
                    ):
                        result = _Resolution("builtin", f"builtins.{builtin_name}")
                    elif imported and not self.in_scope(current):
                        result = _Resolution("external", current)
                    else:
                        result = _Resolution("unresolved", current)
                    break
                binding_owner, binding, suffix = applicable
            binding_key = (binding_owner, binding.name)
            if binding_key in expanded:
                result = _Resolution("cycle", candidate)
                break
            if len(expanded) >= max(1, self.max_depth):
                self.issue(
                    "alias_depth_limit",
                    f"Alias depth {self.max_depth} exceeded: {reference}.",
                )
                result = _Resolution("limit", candidate)
                break
            expanded.add(binding_key)
            current = f"{binding.target}.{suffix}" if suffix else binding.target
            imported = binding.kind == "import"
            scope = "" if imported else binding_owner
        self.resolutions[key] = result
        return result

    def inheritance(self) -> None:
        """Compute bounded C3 ancestry iteratively, diagnosing invalid hierarchies."""
        bases: dict[str, tuple[str, ...]] = {}
        for record in self.source.classes:
            resolved: list[str] = []
            for expression in record.bases:
                reference = _base_reference(expression)
                resolution = self.resolution(record.module, reference)
                if resolution.kind == "local":
                    resolved.append(resolution.reference)
                elif resolution.kind == "external":
                    dependency = resolution.reference.split(".", 1)[0]
                    self.issue(
                        "external_base",
                        f"Base {expression!r} of {record.id} resolves to external "
                        f"{resolution.reference}; inherited members are outside "
                        "the scan. "
                        f"Use --dependency {dependency} or "
                        f"--dependency-source {dependency}=<path> to include source.",
                        record.file,
                        record.line,
                    )
                elif resolution.kind != "builtin":
                    self.issue(
                        "unresolved_base",
                        f"Cannot resolve base {expression!r} of {record.id}.",
                        record.file,
                        record.line,
                    )
            bases[record.id] = tuple(resolved)

        state: dict[str, int] = {}
        for record in self.source.classes:
            if state.get(record.id) == 2:
                continue
            stack: list[tuple[str, int]] = [(record.id, 0)]
            active: set[str] = set()
            while stack:
                class_id, index = stack[-1]
                if index == 0:
                    state[class_id] = 1
                    active.add(class_id)
                class_bases = bases[class_id]
                if index < len(class_bases):
                    base = class_bases[index]
                    stack[-1] = (class_id, index + 1)
                    if base in active:
                        current = self.classes[class_id]
                        self.issue(
                            "inheritance_cycle",
                            f"Inheritance cycle includes {class_id} and {base}.",
                            current.file,
                            current.line,
                        )
                        continue
                    if state.get(base) != 2:
                        if len(stack) > self.max_depth:
                            current = self.classes[class_id]
                            self.issue(
                                "inheritance_depth_limit",
                                f"Depth {self.max_depth} exceeded: {class_id}.",
                                current.file,
                                current.line,
                            )
                            continue
                        stack.append((base, 0))
                    continue
                available = tuple(base for base in class_bases if base in self.mros)
                sequences = [self.mros[base] for base in available]
                sequences.append(available)
                expansion = sum(len(sequence) for sequence in sequences)
                current = self.classes[class_id]
                if self.expansions + expansion > self.budget:
                    ancestry: tuple[str, ...] = ()
                    self.issue(
                        "inheritance_budget_limit",
                        f"Inheritance expansion exceeds {self.budget} records.",
                        current.file,
                        current.line,
                    )
                else:
                    self.expansions += expansion
                    ancestry, consistent = _c3_merge(sequences)
                    if not consistent:
                        self.issue(
                            "inconsistent_mro",
                            f"No consistent Python C3 order exists for {class_id}.",
                            current.file,
                            current.line,
                        )
                        ancestry = ()
                    if len(ancestry) > self.max_depth:
                        self.issue(
                            "inheritance_depth_limit",
                            f"Ancestor limit {self.max_depth} exceeded: {class_id}.",
                            current.file,
                            current.line,
                        )
                        ancestry = ancestry[: self.max_depth]
                self.mros[class_id] = (class_id, *ancestry)
                state[class_id] = 2
                active.remove(class_id)
                stack.pop()

    def spend_surface(self) -> bool:
        """Bound cumulative effective-member work, including repeated ancestry."""
        if self.surface_expansions == self.budget:
            self.issue(
                "surface_budget_limit",
                f"Effective member expansion exceeds {self.budget} records.",
            )
            return False
        self.surface_expansions += 1
        return True

    def callables(self, class_id: str) -> tuple[MethodRecord, ...]:
        """Cache callable variants only for classes reached by traversal."""
        if class_id in self.effective_methods:
            return self.effective_methods[class_id]
        seen: set[str] = set()
        methods: list[MethodRecord] = []
        for ancestor in self.mros[class_id]:
            for name in self.members.get(ancestor, {}):
                if not self.spend_surface():
                    self.effective_methods[class_id] = tuple(methods)
                    return self.effective_methods[class_id]
                seen.add(name)
            for name, variants in self.methods.get(ancestor, {}).items():
                if not self.spend_surface():
                    self.effective_methods[class_id] = tuple(methods)
                    return self.effective_methods[class_id]
                if name.startswith("_") or name in seen:
                    continue
                seen.add(name)
                for index, method in enumerate(variants):
                    if index and not self.spend_surface():
                        self.effective_methods[class_id] = tuple(methods)
                        return self.effective_methods[class_id]
                    if not method.is_property:
                        methods.append(method)
        self.effective_methods[class_id] = tuple(methods)
        return self.effective_methods[class_id]

    def transitions(self, class_id: str) -> tuple[_Transition, ...]:
        """Resolve descriptor targets without copying inherited callable lists."""
        if class_id in self.edges:
            return self.edges[class_id]
        seen: set[str] = set()
        transitions: list[_Transition] = []
        complete = True
        ancestry = self.mros[class_id]
        for ancestor in ancestry:
            for name in self.descriptors[ancestor]:
                if name.startswith("_") or name in seen:
                    continue
                seen.add(name)
                if not self.spend_surface():
                    complete = False
                    break
                # Callable overrides still shadow inherited properties. Indexed
                # lookups inspect at most max_depth + 1 classes per descriptor.
                owner = next(
                    candidate
                    for candidate in ancestry
                    if name in self.members.get(candidate, {})
                    or name in self.methods.get(candidate, {})
                )
                if owner != ancestor:
                    continue
                dynamic = self.members.get(owner, {}).get(name, ())
                if dynamic:
                    for index, member in enumerate(dynamic):
                        if index and not self.spend_surface():
                            break
                        transitions.extend(
                            self.member_targets(
                                self.classes[owner].module,
                                name,
                                (
                                    *member.target_refs,
                                    *_annotation_refs(member.annotation),
                                ),
                                "dynamic_member",
                                member.file,
                                member.line,
                            )
                        )
                else:
                    for index, method in enumerate(
                        self.methods.get(owner, {}).get(name, ())
                    ):
                        if index and not self.spend_surface():
                            break
                        if method.is_property:
                            transitions.extend(
                                self.member_targets(
                                    self.classes[owner].module,
                                    name,
                                    (
                                        *method.returns,
                                        *_annotation_refs(method.return_annotation),
                                    ),
                                    "property",
                                    method.file,
                                    method.line,
                                )
                            )
            if not complete:
                break
        unique = {(edge.member, edge.target): edge for edge in reversed(transitions)}
        self.edges[class_id] = tuple(reversed(tuple(unique.values())))
        return self.edges[class_id]

    def member_targets(
        self,
        module: str,
        member: str,
        references: tuple[str, ...],
        kind: str,
        file: str,
        line: int,
    ) -> list[_Transition]:
        """Resolve evidence references without treating arbitrary callables as types."""
        targets: list[_Transition] = []
        for index, reference in enumerate(dict.fromkeys(references)):
            if index and not self.spend_surface():
                break
            target = self.symbol(module, reference)
            if target:
                targets.append(_Transition(member, target, kind, file, line))
        return targets

    def roots(self, explicit: tuple[str, ...]) -> tuple[str, ...]:
        """Prefer package-level public class exports, then structural graph roots."""
        if explicit:
            explicit_roots: list[str] = []
            seen: set[str] = set()
            for reference in explicit:
                target = self.symbol(self.source.package, reference)
                if not target:
                    raise ValueError(
                        f"Unknown root class {reference!r}; "
                        "use a discovered qualified class ID."
                    )
                if target not in seen:
                    explicit_roots.append(target)
                    seen.add(target)
            return tuple(explicit_roots)
        module = self.modules.get(self.source.package)
        exported: list[str] = []
        if module is not None:
            names = module.exports or tuple(
                dict.fromkeys(
                    [binding.name for binding in module.imports]
                    + [
                        record.name
                        for record in self.source.classes
                        if record.module == module.name
                        and record.qualname == record.name
                    ]
                )
            )
            for name in names:
                target = self.symbol(module.name, name)
                if (
                    not name.startswith("_")
                    and target
                    and (
                        bool(module.exports)
                        or self.target_module(self.classes[target].module)
                    )
                    and any(
                        self.methods.get(ancestor) or self.members.get(ancestor)
                        for ancestor in self.mros[target]
                    )
                ):
                    exported.append(target)
        if exported:
            return tuple(dict.fromkeys(exported))
        incoming = {
            edge.target
            for record in self.source.classes
            if self.target_module(record.module)
            for edge in self.transitions(record.id)
        }
        candidates = tuple(
            record.id
            for record in self.source.classes
            if self.target_module(record.module)
            and not record.name.startswith("_")
            and self.transitions(record.id)
        )
        roots = tuple(target for target in candidates if target not in incoming)
        if roots or candidates:
            return roots or candidates
        return tuple(
            record.id
            for record in self.source.classes
            if self.target_module(record.module) and not record.name.startswith("_")
        )

    def target_module(self, module: str) -> bool:
        """Keep fallback roots within the requested package's source namespace."""
        return module == self.source.package or module.startswith(
            f"{self.source.package}."
        )


def _base_reference(expression: str) -> str:
    """Remove generic arguments and quoted forward-reference syntax from a name."""
    if not expression:
        return ""
    try:
        node = ast.parse(expression, mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return expression
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _base_reference(node.value) if node.value != expression else expression
    while isinstance(node, (ast.Subscript, ast.Call)):
        node = node.value if isinstance(node, ast.Subscript) else node.func
    if isinstance(node, (ast.Name, ast.Attribute)):
        return ast.unparse(node)
    return expression


def _annotation_refs(expression: str) -> tuple[str, ...]:
    """Extract candidate class references from annotations, including unions."""
    if not expression:
        return ()
    try:
        node = ast.parse(expression, mode="eval").body
    except (SyntaxError, ValueError, RecursionError):
        return ()
    refs: list[str] = []
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, (ast.Name, ast.Attribute)):
            refs.append(ast.unparse(current))
        elif isinstance(current, ast.Constant) and isinstance(current.value, str):
            refs.extend(_annotation_refs(current.value))
        elif isinstance(current, ast.BinOp) and isinstance(current.op, ast.BitOr):
            pending.extend((current.right, current.left))
        elif isinstance(current, ast.Subscript):
            outer = ast.unparse(current.value)
            wrapper = outer.rsplit(".", 1)[-1]
            if wrapper in {"Optional", "Union"}:
                children = (
                    current.slice.elts
                    if isinstance(current.slice, ast.Tuple)
                    else (current.slice,)
                )
                pending.extend(reversed(children))
            elif wrapper == "Annotated":
                if isinstance(current.slice, ast.Tuple) and current.slice.elts:
                    pending.append(current.slice.elts[0])
            else:
                # Resource[T] denotes Resource, whereas list[Resource] denotes
                # a list. Descending into every generic invents false edges.
                refs.append(outer)
    return tuple(refs)


def _c3_merge(sequences: list[tuple[str, ...]]) -> tuple[tuple[str, ...], bool]:
    """Merge C3 lists in O(M log B) using indexed tail counts and eligible heads.

    M is total sequence length; B is base count plus one. A conventional merge
    repeatedly scans every tail, producing avoidable quadratic behavior.
    """
    positions = [0] * len(sequences)
    tails: Counter[str] = Counter()
    heads: dict[str, set[int]] = defaultdict(set)
    available: list[int] = []
    for index, sequence in enumerate(sequences):
        if sequence:
            heads[sequence[0]].add(index)
            tails.update(sequence[1:])
    for indexes in heads.values():
        for index in indexes:
            if not tails[sequences[index][0]]:
                heappush(available, index)
    merged: list[str] = []
    while heads:
        while available:
            index = heappop(available)
            position = positions[index]
            if position < len(sequences[index]):
                candidate = sequences[index][position]
                if not tails[candidate]:
                    break
        else:
            return (), False
        merged.append(candidate)
        for index in tuple(heads.pop(candidate)):
            positions[index] += 1
            position = positions[index]
            if position == len(sequences[index]):
                continue
            following = sequences[index][position]
            heads[following].add(index)
            tails[following] -= 1
            if not tails[following]:
                for eligible in heads[following]:
                    heappush(available, eligible)
    return tuple(merged), True


def resolve_inventory(
    source: SourceInventory,
    *,
    root_classes: tuple[str, ...] = (),
    max_depth: int = 32,
    max_paths: int = 100_000,
) -> Inventory:
    """Resolve public APIs and resource paths from immutable source evidence.

    Args:
        source: Parsed source records; no SDK modules are imported or executed.
        root_classes: Qualified IDs or package exports to use as explicit roots.
        max_depth: Maximum resource transitions and inherited ancestry per class.
        max_paths: Maximum graph states, C3 sequence expansion records, and
            cumulative effective-member expansion records (separate budgets).

    Returns:
        An immutable inventory with deterministic paths, declared return fields,
        and explicit diagnostics for external boundaries, unresolved bases,
        cycles, inconsistent MROs, and truncated searches.

    Raises:
        ValueError: A bound is invalid or an explicitly requested root is absent.

    Complexity is output-sensitive. Indexed preprocessing is linear in source
    size; C3 work is bounded by max_paths and O(M log B). Cached ancestry has at
    most max_depth + 1 classes. Callable variants are cached only for reachable
    classes; descriptor work and effective-member copies have a shared max_paths
    budget. Resource traversal has at most max_paths states, each retaining at
    most max_depth + 1 ancestors. No I/O occurs.
    """
    if max_depth < 0 or max_paths < 1:
        raise ValueError(
            "max_depth must be nonnegative and max_paths must be positive."
        )
    resolver = _Resolver(source, max_depth, max_paths)
    resolver.inheritance()
    roots = resolver.roots(root_classes)
    methods: list[ApiMethod] = []
    edges: list[GraphEdge] = []
    pending: deque[tuple[str, str, str, frozenset[str], int]] = deque()
    reserved = 0
    for root in roots:
        if reserved == max_paths:
            resolver.issue(
                "path_limit", f"Resource traversal exceeds {max_paths} paths."
            )
            break
        pending.append((root, root, "client", frozenset((root,)), 0))
        reserved += 1
    while pending:
        root, class_id, path, ancestors, depth = pending.popleft()
        transitions = resolver.transitions(class_id)
        for method in resolver.callables(class_id):
            method_kind = (
                "transport"
                if method.transport
                else "delegating_helper"
                if method.delegates
                else "sdk_helper"
            )
            methods.append(
                ApiMethod(
                    id=f"api:{len(methods)}",
                    root_class=root,
                    resource_class=class_id,
                    defined_in=method.class_id,
                    client_path=f"{path}.{method.name}",
                    method_name=method.name,
                    method_id=method.id,
                    method_kind=method_kind,
                    inherited=method.class_id != class_id,
                )
            )
        if transitions and depth == max_depth:
            resolver.issue(
                "resource_depth_limit",
                f"Resource path {path} of {root} exceeds depth {max_depth}.",
            )
            continue
        for transition in transitions:
            if reserved == max_paths:
                resolver.issue(
                    "path_limit", f"Resource traversal exceeds {max_paths} paths."
                )
                break
            target_path = f"{path}.{transition.member}"
            edges.append(
                GraphEdge(
                    id=f"edge:{len(edges)}",
                    root_class=root,
                    source_class=class_id,
                    target_class=transition.target,
                    source_path=path,
                    target_path=target_path,
                    member=transition.member,
                    kind=transition.kind,
                    file=transition.file,
                    line=transition.line,
                )
            )
            reserved += 1
            if transition.target in ancestors:
                resolver.issue(
                    "resource_cycle",
                    f"Resource path {target_path} revisits {transition.target}.",
                    transition.file,
                    transition.line,
                )
                continue
            pending.append(
                (
                    root,
                    transition.target,
                    target_path,
                    ancestors | {transition.target},
                    depth + 1,
                )
            )
    outputs, output_issues = resolve_outputs(
        source,
        resolve_class=resolver.symbol,
        ancestors=resolver.mros,
        callable_ids=frozenset(method.id for method in source.methods),
        max_records=max_paths,
        classify_reference=resolver.classify,
    )
    return Inventory(
        source,
        tuple(methods),
        tuple(edges),
        (*resolver.issues, *output_issues),
        outputs,
    )
