"""Protect strict auditing of editable source and installed dependency metadata."""

from __future__ import annotations

import importlib.metadata
import json
import runpy
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/audit_dependencies.py"
_COLLECT_REQUIREMENTS = runpy.run_path(str(_SCRIPT))["collect_requirements"]


def _distribution(
    root: Path,
    name: str,
    version: str,
    *,
    editable: bool = False,
) -> importlib.metadata.Distribution:
    path = root / f"{name}-{version}.dist-info"
    path.mkdir()
    (path / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )
    if editable:
        (path / "direct_url.json").write_text(
            json.dumps({"dir_info": {"editable": True}, "url": "file:///example"}),
            encoding="utf-8",
        )
    return importlib.metadata.PathDistribution(path)


def test_audit_excludes_only_local_project_and_sorts_exact_pins(tmp_path: Path) -> None:
    distributions = [
        _distribution(tmp_path, "Zed_Library", "2.3+local"),
        _distribution(tmp_path, "sdk-atlas", "0.1.0", editable=True),
        _distribution(tmp_path, "Alpha.Library", "1.0"),
    ]

    assert _COLLECT_REQUIREMENTS(distributions) == (
        "alpha-library==1.0",
        "zed-library==2.3+local",
    )


def test_audit_rejects_editable_third_party_instead_of_skipping(tmp_path: Path) -> None:
    dependency = _distribution(tmp_path, "external", "1.0", editable=True)

    with pytest.raises(ValueError, match=r"third-party dependency.*editable"):
        _COLLECT_REQUIREMENTS([dependency])


def test_audit_rejects_conflicting_versions_with_normalized_names(
    tmp_path: Path,
) -> None:
    distributions = [
        _distribution(tmp_path, "external_lib", "1.0"),
        _distribution(tmp_path, "external-lib", "2.0"),
    ]

    with pytest.raises(ValueError, match="multiple installed versions"):
        _COLLECT_REQUIREMENTS(distributions)


def test_audit_deduplicates_identical_distribution_metadata(tmp_path: Path) -> None:
    dependency = _distribution(tmp_path, "external", "1.0")

    assert _COLLECT_REQUIREMENTS([dependency, dependency]) == ("external==1.0",)


def test_audit_rejects_invalid_names_before_writing_requirements(
    tmp_path: Path,
) -> None:
    dependency = _distribution(tmp_path, "invalid name", "1.0")

    with pytest.raises(ValueError, match="invalid installed distribution name"):
        _COLLECT_REQUIREMENTS([dependency])


def test_audit_rejects_empty_third_party_inventory(tmp_path: Path) -> None:
    project = _distribution(tmp_path, "sdk-atlas", "0.1.0", editable=True)

    with pytest.raises(ValueError, match="no third-party distributions"):
        _COLLECT_REQUIREMENTS([project])
