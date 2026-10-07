"""Build readable complete snapshots; normalize only source-location metadata."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from sdk_atlas.export import OUTPUT_FILES, write_inventory
from sdk_atlas.graph import resolve_inventory
from sdk_atlas.scanner import scan_source

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden" / "contract_inventory.json"


def contract_inventory(tmp_path: Path) -> Path:
    """Export hostile controlled source without importing fixture modules."""
    source_root = tmp_path / "contract_sdk"
    shutil.copytree(HERE / "fixtures" / "contract_sdk", source_root)
    (source_root / "broken.py.txt").rename(source_root / "broken.py")
    source = scan_source(source_root, "contract_sdk", version="contract-1")
    # No semantic values, definition IDs, positions, diagnostics, or counts are
    # removed. Only the inherently checkout-specific source root is replaced.
    source = replace(source, root="<controlled-source>")
    inventory = resolve_inventory(source, root_classes=("contract_sdk.client.Client",))
    return write_inventory(inventory, tmp_path / "inventory")


def semantic_snapshot(output: Path) -> dict[str, object]:
    """Capture every artifact, including normalized SQLite schema and records."""
    snapshot: dict[str, object] = {}
    for filename in OUTPUT_FILES:
        path = output / filename
        if path.suffix == ".csv":
            with path.open(encoding="utf-8", newline="") as handle:
                snapshot[filename] = list(csv.reader(handle))
        elif path.suffix == ".json":
            snapshot[filename] = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".jsonl":
            snapshot[filename] = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
        elif path.suffix == ".sqlite":
            with closing(sqlite3.connect(path)) as database:
                assert database.execute("PRAGMA integrity_check").fetchall() == [
                    ("ok",)
                ]
                assert database.execute("PRAGMA foreign_key_check").fetchall() == []
                schema = database.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_master "
                    "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
                ).fetchall()
                tables: dict[str, object] = {}
                for kind, name, _, _ in schema:
                    if kind == "table":
                        # Names come from generated sqlite_master, never from
                        # target source. Quote defensively for identifier syntax.
                        quoted = '"' + name.replace('"', '""') + '"'
                        rows = database.execute(f"SELECT * FROM {quoted}").fetchall()
                        tables[name] = sorted(rows, key=lambda row: json.dumps(row))
                snapshot[filename] = {"schema": schema, "tables": tables}
        else:
            raise AssertionError(f"unhandled export artifact: {filename}")
    # JSON canonicalization gives tuples and rows the same representation as
    # the committed, language-neutral snapshot on every supported platform.
    return json.loads(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    # Regeneration is an explicit reviewer action. Ordinary pytest/CI never
    # rewrites the approved oracle when production behavior changes.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", required=True, action="store_true")
    parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sdk-atlas-golden-update-") as temporary:
        regenerated = semantic_snapshot(contract_inventory(Path(temporary)))
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(
        json.dumps(regenerated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
