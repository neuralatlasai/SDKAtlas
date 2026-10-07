"""Verify immutable support-source combination and collision boundaries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sdk_atlas.dependencies import merge_sources
from sdk_atlas.models import ScanIssue, SourceInventory, SourceReference
from sdk_atlas.scanner import scan_source


def _source(tmp_path: Path, package: str) -> SourceInventory:
    root = tmp_path / package.replace(".", "_")
    root.mkdir()
    (root / "__init__.py").write_text(
        """
VALUE = 1
def compute(value: int) -> int:
    return value + VALUE
class Entry:
    declared: str
    def __init__(self):
        self.member = VALUE
    def run(self):
        return self.member
""",
        encoding="utf-8",
    )
    return scan_source(root, package)


def test_merge_preserves_primary_metadata_relative_paths_and_all_records(
    tmp_path: Path,
) -> None:
    primary = replace(
        _source(tmp_path, "target"),
        version="1.2.3",
        issues=(ScanIssue("primary", "primary diagnostic"),),
    )
    dependency = replace(
        _source(tmp_path, "external"),
        version="4.5.6",
        issues=(ScanIssue("dependency", "dependency diagnostic"),),
    )
    original_primary = replace(primary)
    original_dependency = replace(dependency)

    merged = merge_sources(primary, (dependency,))

    assert (merged.package, merged.root, merged.version) == (
        primary.package,
        primary.root,
        primary.version,
    )
    assert merged.support_sources == (
        SourceReference("external", dependency.root, "4.5.6"),
    )
    for field in (
        "modules",
        "classes",
        "methods",
        "dynamic_members",
        "functions",
        "module_members",
        "class_fields",
        "issues",
    ):
        assert getattr(merged, field) == (
            *getattr(primary, field),
            *getattr(dependency, field),
        )
    assert {record.file for record in merged.classes} == {"__init__.py"}
    assert primary == original_primary
    assert dependency == original_dependency


def test_merge_preserves_existing_support_provenance(tmp_path: Path) -> None:
    primary = _source(tmp_path, "target")
    first = _source(tmp_path, "first")
    second = _source(tmp_path, "second")
    third = _source(tmp_path, "third")

    previously_merged = merge_sources(primary, (first,))
    nested_dependency = merge_sources(second, (third,))
    merged = merge_sources(previously_merged, (nested_dependency,))

    assert merged.support_sources == (
        SourceReference("first", first.root),
        SourceReference("second", second.root),
        SourceReference("third", third.root),
    )
    assert tuple(module.name for module in merged.modules) == (
        "target",
        "first",
        "second",
        "third",
    )


@pytest.mark.parametrize(
    ("primary_name", "dependency_name", "reason"),
    [
        ("target", "target", "duplicate source package namespace"),
        ("target", "target.child", "namespaces overlap"),
        ("target.child", "target", "namespaces overlap"),
    ],
)
def test_merge_rejects_duplicate_and_overlapping_namespaces(
    tmp_path: Path, primary_name: str, dependency_name: str, reason: str
) -> None:
    primary = _source(tmp_path, "primary")
    dependency = _source(tmp_path, "dependency")

    with pytest.raises(ValueError, match=reason):
        merge_sources(
            replace(primary, package=primary_name),
            (replace(dependency, package=dependency_name),),
        )


def test_merge_accepts_names_sharing_only_a_partial_component(tmp_path: Path) -> None:
    primary = _source(tmp_path, "target")
    dependency = _source(tmp_path, "targeted")

    assert merge_sources(primary, (dependency,)).support_sources == (
        SourceReference("targeted", dependency.root),
    )


@pytest.mark.parametrize(
    "field",
    [
        "modules",
        "classes",
        "methods",
        "dynamic_members",
        "functions",
        "module_members",
        "class_fields",
    ],
)
def test_merge_rejects_duplicate_record_identities(tmp_path: Path, field: str) -> None:
    primary = _source(tmp_path, "target")
    dependency = _source(tmp_path, "external")
    conflicting = replace(dependency, **{field: getattr(primary, field)})

    with pytest.raises(ValueError, match=r"duplicate .* identity"):
        merge_sources(primary, (conflicting,))

    assert primary.support_sources == ()
    assert dependency.support_sources == ()


@pytest.mark.parametrize("package", ["", "invalid-name", "invalid..name", ".invalid"])
def test_merge_rejects_invalid_namespace_names(tmp_path: Path, package: str) -> None:
    primary = _source(tmp_path, "target")

    with pytest.raises(ValueError, match="invalid source package namespace"):
        merge_sources(replace(primary, package=package), ())


def test_merge_with_no_dependencies_preserves_source_contents(tmp_path: Path) -> None:
    primary = _source(tmp_path, "target")

    assert merge_sources(primary, ()) == primary


def test_merge_rejects_repeated_support_from_a_prior_merge(tmp_path: Path) -> None:
    primary = _source(tmp_path, "target")
    dependency = _source(tmp_path, "external")
    merged = merge_sources(primary, (dependency,))

    with pytest.raises(ValueError, match="duplicate source package namespace"):
        merge_sources(merged, (dependency,))
