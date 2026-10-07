"""Provide the sdk-atlas command-line boundary and actionable diagnostics."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from sdk_atlas import __version__
from sdk_atlas.dependencies import merge_sources
from sdk_atlas.export import summary, write_inventory
from sdk_atlas.graph import resolve_inventory
from sdk_atlas.models import SourceInventory
from sdk_atlas.scanner import scan_source
from sdk_atlas.source import package_source


def _positive(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def _dependency_name(value: str) -> str:
    if any(not part.isidentifier() for part in value.split(".")):
        raise argparse.ArgumentTypeError(
            "dependency must be a dotted Python import name"
        )
    return value


def _dependency_mapping(value: str) -> tuple[str, Path]:
    package, separator, directory = value.partition("=")
    if not separator or not directory:
        raise argparse.ArgumentTypeError("dependency source must use IMPORT_NAME=PATH")
    return _dependency_name(package), Path(directory)


def _scan_dependency(
    package: str,
    path: Path | None,
    *,
    max_files: int,
    max_source_bytes: int,
) -> SourceInventory:
    with package_source(package, source=path) as located:
        source = scan_source(
            located.root,
            package,
            version=located.version,
            max_files=max_files,
            max_source_bytes=max_source_bytes,
        )
    if not source.modules:
        raise ValueError(
            f"dependency {package!r} has no readable Python modules; "
            "inspect source and file limits"
        )
    return source


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory any Python module or package without importing it."
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--package", required=True, help="dotted Python import name")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--source", type=Path, help="repository, package directory, or module/stub file"
    )
    mode.add_argument(
        "--latest",
        action="store_true",
        help="install latest wheel into an isolated temporary target",
    )
    parser.add_argument(
        "--distribution",
        default="",
        help="wheel distribution name if different from the import name",
    )
    parser.add_argument(
        "--dependency",
        type=_dependency_name,
        action="append",
        default=[],
        help="include installed dependency source for static resolution; repeatable",
    )
    parser.add_argument(
        "--dependency-source",
        type=_dependency_mapping,
        action="append",
        default=[],
        metavar="IMPORT_NAME=PATH",
        help="include dependency source from a local path; repeatable, no installation",
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="inventory output directory"
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="qualified or unambiguous root class; repeatable",
    )
    parser.add_argument(
        "--max-depth",
        type=_positive,
        default=32,
        help="resource, inheritance, and alias depth limit",
    )
    parser.add_argument(
        "--max-paths",
        type=_positive,
        default=100_000,
        help="path, inheritance expansion, and effective-member budgets",
    )
    parser.add_argument(
        "--max-files", type=_positive, default=100_000, help="source file limit"
    )
    parser.add_argument(
        "--max-source-bytes",
        type=_positive,
        default=8 * 1024 * 1024,
        help="per-source-file byte limit",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="print the exported summary as JSON",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Scan and export a Python module, returning a process-compatible status code.

    Args:
        argv: Command-line arguments excluding the executable; defaults to argv.

    Returns:
        Zero on success, one for operational failures. Argument parsing exits
        with argparse's conventional zero (help/version) or two (invalid input).

    Filesystem writes are limited to the requested output and temporary staging.
    Dependencies are read only when explicitly requested; they are never imported
    or installed. ``--latest`` applies only to the primary target package.
    Source diagnostics remain visible in summary.json and on standard error.
    """
    args = _parser().parse_args(argv)
    try:
        with package_source(
            args.package,
            source=args.source,
            latest=args.latest,
            distribution=args.distribution,
        ) as located:
            source = scan_source(
                located.root,
                args.package,
                version=located.version,
                max_files=args.max_files,
                max_source_bytes=args.max_source_bytes,
            )
            if not source.modules:
                raise ValueError(
                    "no readable Python modules found; inspect source and file limits"
                )
            dependencies = tuple(
                _scan_dependency(
                    package,
                    path,
                    max_files=args.max_files,
                    max_source_bytes=args.max_source_bytes,
                )
                for package, path in (
                    *((package, None) for package in args.dependency),
                    *args.dependency_source,
                )
            )
            if dependencies:
                source = merge_sources(source, dependencies)
            inventory = resolve_inventory(
                source,
                root_classes=tuple(args.root),
                max_depth=args.max_depth,
                max_paths=args.max_paths,
            )
            write_inventory(inventory, args.out)
            report = summary(inventory)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"sdk-atlas: {error}", file=sys.stderr)
        return 1
    for issue in (*source.issues, *inventory.issues):
        print(
            f"sdk-atlas: {issue.kind}: {issue.file}:{issue.line}: {issue.message}",
            file=sys.stderr,
        )
    if args.print_summary:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0
