"""Verify quality gates reject invalid measurements and infrastructure failures."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import mutation_gate, performance_gate

_ROOT = Path(__file__).resolve().parents[2]


def _performance_config() -> dict[str, object]:
    return json.loads(
        (_ROOT / "tests/quality/performance.json").read_text(encoding="utf-8")
    )


def test_performance_gate_rejects_each_budget_and_nonfinite_measurements() -> None:
    config = _performance_config()
    measurements = {
        "large_latency_ns": 1e20,
        "large_traced_bytes": 1e20,
        "large_methods_per_second": 0,
        "latency_scaling_ratio": 1e20,
        "memory_scaling_ratio": 1e20,
        "large_export_bytes": 1e20,
    }
    assert len(performance_gate.evaluate(config, measurements)) == 6
    for name in measurements:
        measurements[name] = float("nan")
    assert len(performance_gate.evaluate(config, measurements)) == 6
    config["maximum_large_latency_ns"] = float("nan")
    with pytest.raises(ValueError, match="positive number"):
        performance_gate.evaluate(config, measurements)


def test_performance_gate_rejects_fast_incomplete_results(tmp_path: Path) -> None:
    root = tmp_path / "source"
    performance_gate._workload(root, 2, 2, 3)
    inventory = performance_gate._pipeline(root, tmp_path / "output")
    performance_gate._validate(inventory, 2, 2, 3)
    with pytest.raises(ValueError, match="incomplete graph"):
        performance_gate._validate(replace(inventory, api_methods=()), 2, 2, 3)
    with pytest.raises(ValueError, match="missing output"):
        performance_gate._validate(replace(inventory, callable_outputs=()), 2, 2, 3)
    output = next(item for item in inventory.callable_outputs if item.fields)
    changed = replace(output, fields=())
    incomplete = replace(
        inventory,
        callable_outputs=tuple(
            changed if item.callable_id == output.callable_id else item
            for item in inventory.callable_outputs
        ),
    )
    with pytest.raises(ValueError, match="return fields"):
        performance_gate._validate(incomplete, 2, 2, 3)


def test_mutation_target_requires_exact_function_and_single_predicate() -> None:
    source = (
        "class Resolver:\n    def run(self):\n"
        "        if value > 1:\n            return 3\n"
    )
    assert mutation_gate._predicate_line(source, "Resolver.run", "value > 1") == 3
    with pytest.raises(ValueError, match="function anchor"):
        mutation_gate._predicate_line(source, "Resolver.missing", "value > 1")
    with pytest.raises(ValueError, match="predicate is missing"):
        mutation_gate._predicate_line(source, "Resolver.run", "value > 2")
    with pytest.raises(ValueError, match="single predicate"):
        mutation_gate._predicate_line(source, "Resolver.run", "value > 1 or flag")
    repeated = source + "        if value > 1:\n            return 4\n"
    with pytest.raises(ValueError, match="ambiguous"):
        mutation_gate._predicate_line(repeated, "Resolver.run", "value > 1")


@pytest.mark.parametrize(
    ("returncode", "outcome"),
    [(0, "survived"), (1, "killed"), (2, "error"), (3, "error"), (5, "error")],
)
def test_only_failed_tests_kill_mutants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, outcome: str
) -> None:
    def complete(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert "--no-cov" in command
        assert "addopts=" in command
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        assert environment["PYTHONPATH"] == str(tmp_path)
        assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
        return subprocess.CompletedProcess(command, returncode, stdout="result")

    monkeypatch.setattr(mutation_gate.subprocess, "run", complete)
    result = mutation_gate._tests(tmp_path, ["tests/unit/test_scanner.py"], 30)
    assert result.outcome == outcome
    assert result.returncode == returncode


def test_mutant_timeout_is_an_error_not_a_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("pytest", 30, output=b"timed out")

    monkeypatch.setattr(mutation_gate.subprocess, "run", timeout)
    result = mutation_gate._tests(tmp_path, ["tests/unit/test_graph.py"], 30)
    assert result.outcome == "timeout"
    assert result.returncode is None
    assert result.output == "timed out"


def test_mutation_manifest_rejects_duplicate_ids_and_filesystem_escape() -> None:
    config = json.loads(
        (_ROOT / "tests/quality/mutation.json").read_text(encoding="utf-8")
    )
    mutation_gate._validated(config)
    entries = config["mutations"]
    entries.append(dict(entries[0]))
    with pytest.raises(ValueError, match="duplicate mutation id"):
        mutation_gate._validated(config)
    entries.pop()
    entries[0]["file"] = "../scanner.py"
    with pytest.raises(ValueError, match="direct package"):
        mutation_gate._validated(config)
