"""Ensure missing branch evidence cannot satisfy coverage enforcement."""

from __future__ import annotations

import copy
import runpy
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_CHECK = runpy.run_path(str(_ROOT / "scripts/check_coverage.py"))["check"]
_THRESHOLDS = {
    "minimum_lines": 95,
    "minimum_branches": 90,
    "minimum_file_lines": 90,
    "minimum_file_branches": 80,
}


def _complete_report() -> dict[str, object]:
    measured = {
        "covered_lines": 100,
        "num_statements": 100,
        "covered_branches": 100,
        "num_branches": 100,
    }
    return {
        "meta": {"branch_coverage": True},
        "totals": dict(measured),
        "files": {
            path.relative_to(_ROOT).as_posix(): {"summary": dict(measured)}
            for path in (_ROOT / "src/sdk_atlas").rglob("*.py")
        },
    }


def test_branch_floor_fails_even_with_perfect_statement_coverage() -> None:
    report = _complete_report()
    report["totals"]["covered_branches"] = 89
    assert _CHECK(report, _THRESHOLDS) == ["TOTAL: branches 89.00% < 90.00%"]


def test_module_floor_cannot_be_hidden_by_aggregate_coverage() -> None:
    report = _complete_report()
    report["files"]["src/sdk_atlas/scanner.py"]["summary"]["covered_branches"] = 79
    assert _CHECK(report, _THRESHOLDS) == [
        "src/sdk_atlas/scanner.py: branches 79.00% < 80.00%"
    ]


@pytest.mark.parametrize("missing", ["branches", "file"])
def test_incomplete_coverage_evidence_fails_closed(missing: str) -> None:
    report = _complete_report()
    if missing == "branches":
        report["meta"]["branch_coverage"] = False
    else:
        report["files"].pop("src/sdk_atlas/scanner.py")
    with pytest.raises(ValueError):
        _CHECK(report, _THRESHOLDS)


def test_threshold_boundaries_and_windows_paths_use_exact_percentages() -> None:
    report = _complete_report()
    report["totals"]["covered_lines"] = 95
    report["totals"]["covered_branches"] = 90
    report["files"] = {
        name.replace("/", "\\"): value for name, value in report["files"].items()
    }
    original = copy.deepcopy(report)
    assert _CHECK(report, _THRESHOLDS) == []
    assert report == original
    report["totals"]["covered_branches"] = 8999
    report["totals"]["num_branches"] = 10000
    assert _CHECK(report, _THRESHOLDS) == ["TOTAL: branches 89.99% < 90.00%"]
