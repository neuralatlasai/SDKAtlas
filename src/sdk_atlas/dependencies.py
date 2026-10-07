"""Combine explicitly supplied static support sources without imports or mutation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import TypeVar

from sdk_atlas.models import SourceInventory, SourceReference

_Record = TypeVar("_Record")


@dataclass(slots=True)
class _NamespaceNode:
    children: dict[str, _NamespaceNode] = field(default_factory=dict)
    package: str = ""
    descendant: str = ""


def _register_namespace(root: _NamespaceNode, package: str) -> None:
    parts = package.split(".")
    if any(not part.isidentifier() for part in parts):
        raise ValueError(f"invalid source package namespace: {package!r}")
    node = root
    visited = [root]
    for part in parts:
        if node.package:
            raise ValueError(
                f"source package namespaces overlap: {node.package!r} and {package!r}"
            )
        if part not in node.children:
            node.children[part] = _NamespaceNode()
        node = node.children[part]
        visited.append(node)
    if node.package:
        raise ValueError(f"duplicate source package namespace: {package!r}")
    if node.children:
        raise ValueError(
            f"source package namespaces overlap: {package!r} and {node.descendant!r}"
        )
    node.package = package
    for ancestor in visited:
        if not ancestor.descendant:
            ancestor.descendant = package


def _merge_records(
    groups: Iterable[tuple[_Record, ...]],
    *,
    label: str,
    identity: Callable[[_Record], str],
) -> tuple[_Record, ...]:
    records: list[_Record] = []
    identities: set[str] = set()
    for group in groups:
        for record in group:
            key = identity(record)
            if key in identities:
                raise ValueError(
                    f"duplicate {label} identity in source inventories: {key!r}"
                )
            identities.add(key)
            records.append(record)
    return tuple(records)


def merge_sources(
    primary: SourceInventory, support: tuple[SourceInventory, ...]
) -> SourceInventory:
    """Combine primary records with explicitly selected static dependencies.

    Args:
        primary: Target inventory whose package, root, and version remain primary.
        support: Dependency inventories, optionally containing existing support
            provenance. Package namespaces must be disjoint from every source.

    Returns:
        A new immutable inventory preserving input ordering and relative file
        locations. ``support_sources`` maps dependency packages to their original
        roots and versions; support records do not become primary-package roots.

    Raises:
        ValueError: A namespace is invalid, repeated, or overlaps another source,
            or a module/class/callable/member identity occurs more than once.

    Inputs are never mutated. No filesystem, package import, install, or network
    operations occur. Each record and namespace component is visited once;
    dictionary/set lookups have average O(1) cost, with string hashing bounded by
    identifier length. A namespace trie avoids pairwise source-prefix checks.
    """
    sources = (primary, *support)
    references = list(primary.support_sources)
    for source in support:
        references.append(SourceReference(source.package, source.root, source.version))
        references.extend(source.support_sources)

    namespaces = _NamespaceNode()
    _register_namespace(namespaces, primary.package)
    for reference in references:
        _register_namespace(namespaces, reference.package)

    return replace(
        primary,
        modules=_merge_records(
            (source.modules for source in sources),
            label="module",
            identity=lambda record: record.name,
        ),
        classes=_merge_records(
            (source.classes for source in sources),
            label="class",
            identity=lambda record: record.id,
        ),
        methods=_merge_records(
            (source.methods for source in sources),
            label="method",
            identity=lambda record: record.id,
        ),
        dynamic_members=_merge_records(
            (source.dynamic_members for source in sources),
            label="dynamic member",
            identity=lambda record: record.id,
        ),
        issues=tuple(issue for source in sources for issue in source.issues),
        functions=_merge_records(
            (source.functions for source in sources),
            label="function",
            identity=lambda record: record.id,
        ),
        module_members=_merge_records(
            (source.module_members for source in sources),
            label="module member",
            identity=lambda record: record.id,
        ),
        class_fields=_merge_records(
            (source.class_fields for source in sources),
            label="class field",
            identity=lambda record: record.id,
        ),
        support_sources=tuple(references),
    )
