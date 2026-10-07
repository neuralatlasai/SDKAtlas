"""Measure local scan, graph/output resolution, and export stages reproducibly.

Run with the installed/importable project, for example::

    python scripts/benchmark.py --package demo_sdk --source examples --repeats 5

Each iteration creates a fresh temporary export and removes it after measuring
its size. Timings use perf_counter_ns and exclude package location, statistics,
output-size inspection, and temporary-directory cleanup. Warmups execute every
stage but do not enter reported statistics. SDK target modules are never
imported; this helper has no package-installation or network mode.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns

from sdk_atlas import __version__
from sdk_atlas.export import summary, write_inventory
from sdk_atlas.graph import resolve_inventory
from sdk_atlas.scanner import scan_source
from sdk_atlas.source import package_source

_MAX_REPEATS = 50
_MAX_WARMUP = 10
_NANOSECONDS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class _Sample:
    scan_ns: int
    resolve_ns: int
    export_ns: int
    output_bytes: int
    modules: int
    api_methods: int
    callable_outputs: int
    counts: object

    @property
    def total_ns(self) -> int:
        """Return the sum of measured stages, excluding benchmark bookkeeping."""
        return self.scan_ns + self.resolve_ns + self.export_ns


def _repeats(value: str) -> int:
    count = int(value)
    if not 1 <= count <= _MAX_REPEATS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {_MAX_REPEATS}")
    return count


def _warmup(value: str) -> int:
    count = int(value)
    if not 0 <= count <= _MAX_WARMUP:
        raise argparse.ArgumentTypeError(f"must be between 0 and {_MAX_WARMUP}")
    return count


def _sample(
    root: Path, package: str, version: str, root_classes: tuple[str, ...]
) -> _Sample:
    # Cleanup happens per iteration, so repeated runs never accumulate exports
    # on disk. Temporary-directory creation is outside the measured stages.
    with tempfile.TemporaryDirectory(prefix="sdk-atlas-benchmark-") as temporary:
        started = perf_counter_ns()
        source = scan_source(root, package, version=version)
        scanned = perf_counter_ns()
        if not source.modules:
            raise ValueError("no readable modules found for the benchmark target")
        inventory = resolve_inventory(source, root_classes=root_classes)
        resolved = perf_counter_ns()
        output = write_inventory(inventory, Path(temporary) / "inventory")
        exported = perf_counter_ns()
        output_bytes = sum(path.stat().st_size for path in output.iterdir())
        counts = summary(inventory)["counts"]
        return _Sample(
            scan_ns=scanned - started,
            resolve_ns=resolved - scanned,
            export_ns=exported - resolved,
            output_bytes=output_bytes,
            modules=len(source.modules),
            api_methods=len(inventory.api_methods),
            callable_outputs=len(inventory.callable_outputs),
            counts=counts,
        )


def _statistics(values: Sequence[int]) -> dict[str, int | float]:
    return {
        "median": statistics.median(values),
        "min": min(values),
        "max": max(values),
    }


def _report(samples: Sequence[_Sample]) -> dict[str, object]:
    scan = _statistics([sample.scan_ns for sample in samples])
    resolution = _statistics([sample.resolve_ns for sample in samples])
    exporting = _statistics([sample.export_ns for sample in samples])
    last = samples[-1]
    # Positive denominators also cover the theoretical zero-tick duration on
    # an unusually coarse clock without emitting NaN or Infinity into JSON.
    scan_seconds = max(1, scan["median"]) / _NANOSECONDS_PER_SECOND
    resolve_seconds = max(1, resolution["median"]) / _NANOSECONDS_PER_SECOND
    export_seconds = max(1, exporting["median"]) / _NANOSECONDS_PER_SECOND
    return {
        "timings_ns": {
            "scan": scan,
            "resolve_and_outputs": resolution,
            "export": exporting,
            "total": _statistics([sample.total_ns for sample in samples]),
        },
        "counts": last.counts,
        "output_bytes": _statistics([sample.output_bytes for sample in samples]),
        "rates": {
            "scan_modules_per_second": last.modules / scan_seconds,
            "resolved_api_methods_per_second": last.api_methods / resolve_seconds,
            "resolved_output_records_per_second": last.callable_outputs
            / resolve_seconds,
            "export_bytes_per_second": statistics.median(
                sample.output_bytes for sample in samples
            )
            / export_seconds,
        },
        "samples_ns": [
            {
                "scan": sample.scan_ns,
                "resolve_and_outputs": sample.resolve_ns,
                "export": sample.export_ns,
                "total": sample.total_ns,
            }
            for sample in samples
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Print bounded local benchmark statistics as JSON and return process status.

    Args:
        argv: Optional command arguments; defaults to the process arguments.
            ``--package`` names the target. ``--source`` selects a local source
            tree/file; omission uses a filesystem installation. ``--root`` is
            repeatable. ``--repeats`` accepts 1..50 and ``--warmup`` accepts 0..10.

    Returns:
        Zero after reporting all measured runs; one for an operational failure.
        Argument parsing uses its conventional zero/help or two/error exits.

    Side Effects:
        Reads local sources and creates/deletes isolated temporary artifacts.
        Target imports, installations, and network operations are not performed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, help="dotted Python import name")
    parser.add_argument(
        "--source", type=Path, help="local repository, package, or module"
    )
    parser.add_argument(
        "--distribution", default="", help="distribution name for version metadata"
    )
    parser.add_argument(
        "--root", action="append", default=[], help="root class; repeatable"
    )
    parser.add_argument(
        "--repeats", type=_repeats, default=5, help="measured runs (1..50)"
    )
    parser.add_argument("--warmup", type=_warmup, default=1, help="warmup runs (0..10)")
    arguments = parser.parse_args(argv)
    roots = tuple(arguments.root)
    samples: list[_Sample] = []
    try:
        with package_source(
            arguments.package,
            source=arguments.source,
            distribution=arguments.distribution,
        ) as located:
            for iteration in range(arguments.warmup + arguments.repeats):
                measured = _sample(
                    located.root, arguments.package, located.version, roots
                )
                if iteration < arguments.warmup:
                    continue
                if samples and measured.counts != samples[0].counts:
                    raise ValueError("inventory counts changed between benchmark runs")
                samples.append(measured)
            report = {
                "schema_version": 1,
                "sdk_atlas_version": __version__,
                "package": arguments.package,
                "target_version": located.version,
                "source_root": str(located.root),
                "root_classes": roots,
                "repeats": arguments.repeats,
                "warmup": arguments.warmup,
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "machine": platform.machine(),
                "temporary_root": tempfile.gettempdir(),
                "clock": "perf_counter_ns",
                **_report(samples),
            }
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print(f"sdk-atlas benchmark: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
