"""Run a bounded Cosmic Ray campaign against selected production predicates.

This is a scoped regression campaign, not a whole-project mutation score. Every
configured target must still match one real function and predicate; source drift
fails rather than silently reducing the denominator. Cosmic Ray's AddNot operator
creates the mutants. The gate copies production sources to a temporary tree and
runs the real tests against that copy, leaving the checkout untouched. Baseline
tests must pass. Only pytest's test-failure exit (1) kills a mutant: infrastructure
errors, collection errors, missing tests, and timeouts fail the entire campaign.
Coverage is disabled in the subprocesses because coverage shortfalls cannot
prove a mutant was detected. Configured compound predicates are prohibited:
AddNot does not parenthesize a compound expression and could alter only a term.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = _ROOT / "tests" / "quality" / "mutation.json"


@dataclass(frozen=True, slots=True)
class TestResult:
    """Keep a bounded subprocess outcome independent from mutation scoring."""

    outcome: str
    returncode: int | None
    elapsed_ns: int
    output: str


def _predicate_line(source: str, function: str, predicate: str) -> int:
    """Find exactly one configured predicate within its lexical function path."""
    current: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef = (
        ast.parse(source)
    )
    for segment in function.split("."):
        matches = [
            node
            for node in current.body
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == segment
        ]
        if len(matches) != 1:
            raise ValueError(
                f"mutation function anchor is missing/ambiguous: {function}"
            )
        current = matches[0]
    expected = ast.parse(predicate, mode="eval").body
    if isinstance(expected, ast.BoolOp):
        raise ValueError("AddNot campaign requires a single predicate")
    key = ast.dump(expected, include_attributes=False)
    positions = [
        node.test.lineno
        for node in ast.walk(current)
        if isinstance(node, ast.If)
        and ast.dump(node.test, include_attributes=False) == key
    ]
    if len(positions) != 1:
        raise ValueError(
            f"mutation predicate is missing/ambiguous: {function}: {predicate}"
        )
    return positions[0]


def _mutant(source: str, function: str, predicate: str) -> str:
    """Let the external framework generate the selected, syntax-valid mutant."""
    from cosmic_ray.ast import ast_nodes, get_ast  # type: ignore[import-untyped]
    from cosmic_ray.mutating import mutate_code  # type: ignore[import-untyped]
    from cosmic_ray.operators.boolean_replacer import (  # type: ignore[import-untyped]
        AddNot,
    )

    target_line = _predicate_line(source, function, predicate)
    operator = AddNot()
    occurrence = 0
    found: list[int] = []
    for node in ast_nodes(get_ast(source)):
        for start, _end in operator.mutation_positions(node):
            if start[0] == target_line:
                found.append(occurrence)
            occurrence += 1
    if len(found) != 1:
        raise ValueError(
            "Cosmic Ray could not uniquely target the configured predicate"
        )
    mutated: str | None = mutate_code(source, operator, found[0])
    if mutated is None or mutated == source:
        raise ValueError("Cosmic Ray produced no mutation")
    ast.parse(mutated)
    return mutated


def _tests(source_root: Path, targets: Sequence[str], timeout: int) -> TestResult:
    environment = os.environ.copy()
    # Prepending the isolated copy also overrides editable-install path hooks.
    environment["PYTHONPATH"] = str(source_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTEST_ADDOPTS"] = ""
    started = perf_counter_ns()
    command = [
        sys.executable,
        "-B",
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "--no-cov",
        "--import-mode=importlib",
        "--strict-config",
        "--strict-markers",
        "-xq",
        *targets,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or b""
        detail = output.decode("utf-8", "replace")
        return TestResult("timeout", None, perf_counter_ns() - started, detail[-16000:])
    outcome = {0: "survived", 1: "killed"}.get(completed.returncode, "error")
    return TestResult(
        outcome,
        completed.returncode,
        perf_counter_ns() - started,
        completed.stdout[-16000:],
    )


def _text(config: Mapping[str, object], name: str) -> str:
    value = config[name]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _validated(
    config: Mapping[str, object],
) -> tuple[list[dict[str, object]], int, float]:
    if config.get("schema_version") != 1 or config.get("framework") != "cosmic-ray":
        raise ValueError("unsupported mutation configuration")
    if config.get("operator") != "AddNot":
        raise ValueError("this campaign supports only Cosmic Ray AddNot")
    entries = config.get("mutations")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
        raise ValueError("campaign must contain 1..64 mutation entries")
    timeout = config.get("timeout_seconds")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= 120
    ):
        raise ValueError("timeout_seconds must be an integer in 1..120")
    score = config.get("minimum_kill_percent")
    if (
        isinstance(score, bool)
        or not isinstance(score, int | float)
        or not 1 <= score <= 100
    ):
        raise ValueError("minimum_kill_percent must be in 1..100")
    mutations: list[dict[str, object]] = []
    identifiers: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("mutation entries must be objects")
        identifier = _text(entry, "id")
        if identifier in identifiers:
            raise ValueError(f"duplicate mutation id: {identifier}")
        identifiers.add(identifier)
        name = _text(entry, "file")
        # The manifest never chooses arbitrary subprocess commands or files.
        if Path(name).name != name or not name.endswith(".py"):
            raise ValueError("mutation file must be a direct package Python module")
        test = _text(entry, "test")
        target = (_ROOT / test).resolve(strict=True)
        if not target.is_relative_to(_ROOT / "tests") or not target.is_file():
            raise ValueError("mutation tests must be existing files within tests")
        _text(entry, "function")
        _text(entry, "predicate")
        mutations.append(entry)
    return mutations, timeout, float(score)


def run(config: Mapping[str, object]) -> dict[str, object]:
    mutations, timeout, minimum_score = _validated(config)
    framework_version = importlib.metadata.version("cosmic-ray")
    outcomes: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="sdk-atlas-mutation-") as temporary:
        source_root = Path(temporary) / "src"
        shutil.copytree(
            _ROOT / "src",
            source_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # Baseline covers the union of selected tests using the exact same copy
        # and invocation policy as every mutant. Any failure aborts the campaign.
        targets = sorted({_text(mutation, "test") for mutation in mutations})
        baseline = _tests(source_root, targets, timeout)
        if baseline.outcome != "survived":
            raise ValueError(
                f"unmutated baseline failed ({baseline.outcome}): {baseline.output}"
            )
        for mutation in mutations:
            module = source_root / "sdk_atlas" / _text(mutation, "file")
            original = module.read_text(encoding="utf-8")
            mutated = _mutant(
                original, _text(mutation, "function"), _text(mutation, "predicate")
            )
            module.write_text(mutated, encoding="utf-8")
            try:
                result = _tests(source_root, [_text(mutation, "test")], timeout)
            finally:
                module.write_text(original, encoding="utf-8")
            outcome = {
                **mutation,
                "outcome": result.outcome,
                "returncode": result.returncode,
                "elapsed_ns": result.elapsed_ns,
                "output": result.output,
            }
            outcomes.append(outcome)
            print(f"{mutation['id']}: {result.outcome}", file=sys.stderr, flush=True)
    killed = sum(outcome["outcome"] == "killed" for outcome in outcomes)
    errors = sum(outcome["outcome"] in {"error", "timeout"} for outcome in outcomes)
    score = 100 * killed / len(mutations)
    return {
        "schema_version": 1,
        "framework": "cosmic-ray",
        "framework_version": framework_version,
        "operator": "AddNot",
        "scope": (
            "configured production predicates only; not whole-project mutation coverage"
        ),
        "baseline_elapsed_ns": baseline.elapsed_ns,
        "mutations": outcomes,
        "killed": killed,
        "total": len(mutations),
        "infrastructure_errors": errors,
        "kill_percent": score,
        "minimum_kill_percent": minimum_score,
        "passed": errors == 0 and score >= minimum_score,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args(argv)
    try:
        config = json.loads(arguments.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("mutation configuration must be an object")
        report = run(config)
    except (OSError, ValueError, KeyError, ImportError) as error:
        print(f"sdk-atlas mutation gate: {error}", file=sys.stderr)
        return 1
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
