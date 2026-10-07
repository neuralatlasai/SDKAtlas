"""Detect semantic drift in every CSV, JSON, and relational export artifact."""

import json

import pytest

from sdk_atlas.export import OUTPUT_FILES

from .golden_support import GOLDEN, contract_inventory, semantic_snapshot


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    return semantic_snapshot(contract_inventory(tmp_path_factory.mktemp("golden")))


@pytest.mark.parametrize("artifact", OUTPUT_FILES)
def test_complete_semantic_inventory_matches_reviewed_golden(
    artifact: str, snapshot: dict[str, object]
) -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(snapshot) == set(expected) == set(OUTPUT_FILES)
    assert snapshot[artifact] == expected[artifact], (
        f"{artifact} changed; review the semantic diff before explicitly "
        "regenerating tests/regression/golden/contract_inventory.json"
    )


def test_golden_covers_nonempty_evidence_and_visible_failure_domains(
    snapshot: dict[str, object],
) -> None:
    """Prevent an empty or reduced fixture from making broad snapshots vacuous."""
    report = snapshot["summary.json"]
    assert isinstance(report, dict)
    counts = report["counts"]
    assert counts["method_definitions"] >= 15
    assert counts["functions"] >= 6
    assert counts["class_fields"] >= 8
    assert counts["api_methods"] >= 15
    assert counts["transport_evidence"] >= 2
    assert {issue["kind"] for issue in report["issues"]} >= {
        "source_error",
        "external_base",
        "resource_cycle",
    }
