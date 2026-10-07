"""Enforce bounded latency, Python allocation, and scaling regression budgets.

The checked-in workload exercises discovery, AST scanning, aliases, inheritance,
resource traversal, shared response fields, and all inventory exports. Timing
and tracemalloc runs are separate: allocation instrumentation must not inflate
the latency metric. Budgets are intentionally generous for shared CI runners;
they detect substantial regressions, rather than rank machines. Tracemalloc
measures Python allocations, not process RSS, allocator fragmentation, or native
SQLite allocations. Workload creation and semantic validation are not timed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import tempfile
import tracemalloc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns

from sdk_atlas.export import write_inventory
from sdk_atlas.graph import resolve_inventory
from sdk_atlas.models import Inventory
from sdk_atlas.scanner import scan_source

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _ROOT / "tests" / "quality" / "performance.json"


@dataclass(frozen=True, slots=True)
class Sample:
    """Preserve a complete-pipeline measurement and semantic output digest."""

    elapsed_ns: int
    export_bytes: int
    digest: tuple[tuple[str, str], ...]


def _workload(root: Path, modules: int, methods: int, fields: int) -> None:
    """Build linear-size source with shared typed responses and resource aliases."""
    root.mkdir()
    (root / "models.py").write_text(
        "class Payload:\n"
        + "".join(f"    field_{index}: int\n" for index in range(fields)),
        encoding="utf-8",
    )
    imports: list[str] = []
    assignments: list[str] = []
    for index in range(modules):
        imports.append(f"from .part_{index} import Resource as Alias{index}\n")
        assignments.append(f"        self.part_{index} = Alias{index}()\n")
        (root / f"part_{index}.py").write_text(
            "from .models import Payload as Response\n"
            "class Base:\n"
            "    def inherited(self, value: int = 0) -> Response: ...\n"
            "class Resource(Base):\n"
            + "".join(
                f"    def method_{method}(self, value: int, *, flag: bool = True)"
                " -> Response: ...\n"
                for method in range(methods - 1)
            ),
            encoding="utf-8",
        )
    (root / "__init__.py").write_text(
        "".join(imports) + "__all__ = ['Client']\nclass Client:\n"
        "    def __init__(self) -> None:\n" + "".join(assignments),
        encoding="utf-8",
    )


def _pipeline(root: Path, destination: Path) -> Inventory:
    inventory = resolve_inventory(
        scan_source(root, "budget_sdk"), root_classes=("budget_sdk.Client",)
    )
    write_inventory(inventory, destination)
    return inventory


def _validate(inventory: Inventory, modules: int, methods: int, fields: int) -> None:
    """Prevent a faster but incomplete inventory from satisfying the budgets."""
    expected = modules * methods
    if len(inventory.api_methods) != expected:
        raise ValueError(f"expected {expected} API methods; got incomplete graph")
    source = inventory.source
    if len(source.modules) != modules + 2 or len(source.methods) != expected + 1:
        raise ValueError("source definitions changed in the performance workload")
    outputs = {output.callable_id: output for output in inventory.callable_outputs}
    if len(outputs) != len(source.methods):
        raise ValueError("a method is missing output analysis")
    for method in inventory.api_methods:
        output = outputs[method.method_id]
        if output.status != "resolved" or len(output.fields) != fields:
            raise ValueError("expected return fields changed in performance workload")
    if inventory.issues or source.issues:
        raise ValueError("performance workload produced unexpected diagnostics")


def _sample(
    root: Path, destination: Path, modules: int, methods: int, fields: int
) -> Sample:
    started = perf_counter_ns()
    inventory = _pipeline(root, destination)
    elapsed_ns = perf_counter_ns() - started
    _validate(inventory, modules, methods, fields)
    paths = sorted(destination.iterdir(), key=lambda path: path.name)
    digest = tuple(
        (path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths
    )
    return Sample(elapsed_ns, sum(path.stat().st_size for path in paths), digest)


def _memory(
    root: Path, destination: Path, modules: int, methods: int, fields: int
) -> int:
    tracemalloc.start()
    try:
        inventory = _pipeline(root, destination)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    _validate(inventory, modules, methods, fields)
    return peak


def _number(config: Mapping[str, object], name: str) -> float:
    value = config[name]
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive number")
    return float(value)


def evaluate(
    config: Mapping[str, object], measurements: Mapping[str, float]
) -> list[str]:
    """Return every failed budget instead of masking later failures on first miss."""
    conditions = (
        ("large_latency_ns", "maximum_large_latency_ns", False),
        ("large_traced_bytes", "maximum_large_traced_bytes", False),
        ("large_methods_per_second", "minimum_large_methods_per_second", True),
        ("latency_scaling_ratio", "maximum_latency_scaling_ratio", False),
        ("memory_scaling_ratio", "maximum_memory_scaling_ratio", False),
        ("large_export_bytes", "maximum_export_bytes", False),
    )
    failures: list[str] = []
    for measurement, budget, minimum in conditions:
        observed = measurements[measurement]
        limit = _number(config, budget)
        if not math.isfinite(observed) or observed < 0:
            failures.append(f"{measurement} must be a finite nonnegative measurement")
            continue
        if (observed < limit) if minimum else (observed > limit):
            failures.append(f"{measurement}={observed:.3f} violates {budget}={limit}")
    return failures


def run(config: Mapping[str, object]) -> dict[str, object]:
    """Measure two workload sizes, repeated output determinism, and scaling."""
    if config.get("schema_version") != 1:
        raise ValueError("unsupported performance budget schema")
    names = (
        "small_modules",
        "large_modules",
        "methods_per_module",
        "response_fields",
        "repeats",
    )
    sizes: dict[str, int] = {}
    for name in names:
        value = _number(config, name)
        if not value.is_integer() or value > 1024:
            raise ValueError(f"{name} must be an integer in 1..1024")
        sizes[name] = int(value)
    if not 2 <= sizes["repeats"] <= 10:
        raise ValueError("repeats must be in 2..10")
    if sizes["methods_per_module"] < 2:
        raise ValueError("methods_per_module must be at least two")
    if sizes["large_modules"] < 2 * sizes["small_modules"]:
        raise ValueError("large workload must be at least twice the small workload")
    methods = sizes["methods_per_module"]
    fields = sizes["response_fields"]
    medians: dict[str, float] = {}
    peaks: dict[str, int] = {}
    output_sizes: dict[str, int] = {}
    samples_report: dict[str, list[int]] = {}
    with tempfile.TemporaryDirectory(prefix="sdk-atlas-performance-") as temporary:
        directory = Path(temporary)
        for label in ("small", "large"):
            modules = sizes[f"{label}_modules"]
            root = directory / label
            _workload(root, modules, methods, fields)
            warmup = _sample(
                root, directory / f"{label}-warmup", modules, methods, fields
            )
            samples: list[Sample] = []
            for repeat in range(sizes["repeats"]):
                sample = _sample(
                    root, directory / f"{label}-{repeat}", modules, methods, fields
                )
                if sample.digest != warmup.digest:
                    raise ValueError("semantic export drift between repeated workloads")
                samples.append(sample)
            medians[label] = statistics.median(sample.elapsed_ns for sample in samples)
            output_sizes[label] = samples[-1].export_bytes
            samples_report[label] = [sample.elapsed_ns for sample in samples]
            peaks[label] = _memory(
                root, directory / f"{label}-memory", modules, methods, fields
            )
    measurements = {
        "large_latency_ns": medians["large"],
        "large_traced_bytes": float(peaks["large"]),
        "large_methods_per_second": sizes["large_modules"]
        * methods
        * 1e9
        / max(1, medians["large"]),
        "latency_scaling_ratio": medians["large"] / max(10_000_000, medians["small"]),
        "memory_scaling_ratio": peaks["large"] / max(1, peaks["small"]),
        "large_export_bytes": float(output_sizes["large"]),
    }
    failures = evaluate(config, measurements)
    return {
        "schema_version": 1,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "budgets": dict(config),
        "measurements": measurements,
        "latency_samples_ns": samples_report,
        "peak_traced_bytes": peaks,
        "semantic_checks": (
            "counts, return fields, diagnostics, all export SHA-256 repeatability"
        ),
        "failures": failures,
        "passed": not failures,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)
    try:
        config = json.loads(arguments.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("performance configuration must be an object")
        report = run(config)
    except (OSError, ValueError, KeyError) as error:
        print(f"sdk-atlas performance gate: {error}", file=sys.stderr)
        return 1
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
