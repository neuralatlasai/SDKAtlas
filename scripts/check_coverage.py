"""Enforce independent aggregate and per-module statement/branch coverage floors.

Run after the complete pytest suite. Reading coverage.py's JSON avoids treating
its combined percentage as a branch-coverage guarantee. Every production module
must appear in the report, including modules never imported by the tests.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MAX_REPORT_BYTES = 16 * 1024 * 1024


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return dict(value)


def _percentage(summary: dict[str, object], covered: str, total: str) -> float:
    count, denominator = summary.get(covered), summary.get(total)
    if (
        type(count) is not int
        or type(denominator) is not int
        or not 0 <= count <= denominator
    ):
        raise ValueError(f"invalid coverage counters: {covered}, {total}")
    return 100.0 * count / denominator if denominator else 100.0


def check(report: object, thresholds: object) -> list[str]:
    """Return threshold violations; malformed/missing coverage evidence raises.

    Args:
        report: Unmodified JSON document from the complete pytest-cov run.
        thresholds: Repository-controlled numeric percentages from pyproject.toml.

    Returns:
        All failing aggregate/module comparisons, with measured percentages.

    Raises:
        ValueError: Evidence is incomplete, invalid, or not branch-enabled.

    No filesystem writes or imports of the measured production package occur.
    """
    document = _mapping(report, "coverage report")
    if _mapping(document.get("meta"), "metadata").get("branch_coverage") is not True:
        raise ValueError("coverage report must include branch measurements")
    configured = _mapping(thresholds, "coverage thresholds")
    limits: dict[str, float] = {}
    for name in (
        "minimum_lines",
        "minimum_branches",
        "minimum_file_lines",
        "minimum_file_branches",
    ):
        value = configured.get(name)
        if (
            not isinstance(value, int | float)
            or isinstance(value, bool)
            or not math.isfinite(value)
            or not 0 <= value <= 100
        ):
            raise ValueError(f"invalid percentage threshold: {name}")
        limits[name] = float(value)

    files = _mapping(document.get("files"), "file coverage")
    expected = {
        path.relative_to(_ROOT).as_posix()
        for path in (_ROOT / "src/sdk_atlas").rglob("*.py")
    }
    normalized = {name.replace("\\", "/"): value for name, value in files.items()}
    if set(normalized) != expected:
        raise ValueError(
            "coverage file set differs from production sources; "
            f"missing={sorted(expected - normalized.keys())}, "
            f"unexpected={sorted(normalized.keys() - expected)}"
        )
    failures: list[str] = []
    summaries = [("TOTAL", _mapping(document.get("totals"), "totals"), "minimum_")]
    summaries.extend(
        (
            name,
            _mapping(_mapping(value, name).get("summary"), f"{name} summary"),
            "minimum_file_",
        )
        for name, value in sorted(normalized.items())
    )
    for label, measured, prefix in summaries:
        for kind, covered, total in (
            ("lines", "covered_lines", "num_statements"),
            ("branches", "covered_branches", "num_branches"),
        ):
            actual = _percentage(measured, covered, total)
            required = limits[prefix + kind]
            if actual < required:
                failures.append(f"{label}: {kind} {actual:.2f}% < {required:.2f}%")
    return failures


def main(argv: list[str] | None = None) -> int:
    """Read configured evidence and fail closed on missing data or unmet floors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=_ROOT / ".cache/coverage.json")
    arguments = parser.parse_args(argv)
    try:
        with arguments.report.open("rb") as handle:
            data = handle.read(_MAX_REPORT_BYTES + 1)
        if len(data) > _MAX_REPORT_BYTES:
            raise ValueError("coverage report exceeds the 16 MiB input bound")
        with (_ROOT / "pyproject.toml").open("rb") as handle:
            configuration = tomllib.load(handle)
        failures = check(
            json.loads(data), configuration["tool"]["sdk_atlas"]["coverage-gate"]
        )
    except (OSError, ValueError, KeyError) as error:
        print(f"Coverage gate could not validate evidence: {error}", file=sys.stderr)
        return 1
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(
        "Coverage gate passed: aggregate lines >=95%, branches >=90%; "
        "each module lines >=90%, branches >=80%."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
