"""Replay parser/resolver fuzz bytes without importing or executing their code.

Run a saved corpus with ``python tests/fuzz/harness.py FILE [FILE ...]``. The
``fuzz_one`` function is also directly reusable by coverage-guided Python engines.
Any unexpected exception or violated referential invariant fails the input; only
the scanner's documented source diagnostics represent malformed-input recovery.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from sdk_atlas.graph import resolve_inventory
from sdk_atlas.models import Inventory
from sdk_atlas.scanner import scan_source

MAX_FUZZ_BYTES = 8192
MAX_FUZZ_AST_NODES = 2048
MAX_FUZZ_PATHS = 64


def fuzz_one(data: bytes) -> Inventory:
    """Exercise parsing, aliases, output resolution, and graph traversal together."""
    if len(data) > MAX_FUZZ_BYTES:
        raise ValueError(f"Fuzz input exceeds {MAX_FUZZ_BYTES} bytes.")
    with TemporaryDirectory(prefix="sdk-atlas-fuzz-") as directory:
        path = Path(directory) / "sample.py"
        path.write_bytes(data)
        source = scan_source(
            path,
            "sample",
            max_source_bytes=MAX_FUZZ_BYTES,
            max_ast_nodes=MAX_FUZZ_AST_NODES,
        )
        inventory = resolve_inventory(source, max_depth=8, max_paths=MAX_FUZZ_PATHS)
        # Repeated runs over the same bytes must preserve evidence and ordering.
        assert inventory == resolve_inventory(
            scan_source(
                path,
                "sample",
                max_source_bytes=MAX_FUZZ_BYTES,
                max_ast_nodes=MAX_FUZZ_AST_NODES,
            ),
            max_depth=8,
            max_paths=MAX_FUZZ_PATHS,
        )
    classes = {record.id for record in source.classes}
    callables = {record.id for record in (*source.methods, *source.functions)}
    methods = {record.id for record in source.methods}
    assert {record.callable_id for record in inventory.callable_outputs} == callables
    assert all(record.class_id in classes for record in source.methods)
    assert all(record.class_id in classes for record in source.class_fields)
    assert all(record.method_id in methods for record in inventory.api_methods)
    assert all(record.defined_in in classes for record in inventory.api_methods)
    assert all(
        record.source_class in classes and record.target_class in classes
        for record in inventory.resource_graph
    )
    assert len(inventory.resource_graph) < MAX_FUZZ_PATHS
    return inventory


def main() -> None:
    """Read each explicit corpus file with the same bound used by the scanner."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", nargs="+", type=Path)
    arguments = parser.parse_args()
    for path in arguments.corpus:
        with path.open("rb") as handle:
            data = handle.read(MAX_FUZZ_BYTES + 1)
        fuzz_one(data)


if __name__ == "__main__":
    main()
