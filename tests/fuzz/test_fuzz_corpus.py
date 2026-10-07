"""Replay permanent edge cases and generate bounded parser/resolver input."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sdk_atlas.graph import resolve_inventory
from sdk_atlas.scanner import scan_source

from .harness import MAX_FUZZ_BYTES, fuzz_one


@pytest.mark.parametrize(
    ("data", "expected_issue"),
    [
        (b"", ""),
        (b"\x00\xff\xfe", "source_error"),
        (b"# coding: missing-codec\nclass Client: pass\n", "source_error"),
        (b"# coding: ascii\n# \xff\n", "source_error"),
        (b"class Broken(\n", "source_error"),
        (b"if True:\n\tpass\n        pass\n", "source_error"),
        (b"value = " + b"[" * 300 + b"0" + b"]" * 300, "source_error"),
        (
            b"A = B\nB = A\nclass Client(A):\n    def ping(self): ...\n",
            "unresolved_base",
        ),
        (
            b"class Client:\n    @property\n    def loop(self) -> Client: ...\n",
            "resource_cycle",
        ),
        (b"class Client:\n    def malformed(self) -> 'list[': ...\n", ""),
        (
            b"raise RuntimeError('fuzz source must never execute')\n"
            b"class Client:\n    def ping(self) -> int: ...\n",
            "",
        ),
    ],
    ids=[
        "empty",
        "nul-invalid-utf8",
        "unknown-encoding",
        "invalid-declared-encoding",
        "unterminated-class",
        "inconsistent-indentation",
        "parser-nesting-limit",
        "alias-cycle",
        "resource-cycle",
        "malformed-forward-annotation",
        "execution-sentinel",
    ],
)
def test_permanent_adversarial_corpus(data: bytes, expected_issue: str) -> None:
    inventory = fuzz_one(data)
    if expected_issue:
        assert expected_issue in {
            issue.kind for issue in (*inventory.source.issues, *inventory.issues)
        }


@given(data=st.binary(max_size=4096))
def test_arbitrary_bytes_preserve_determinism_and_inventory_references(
    data: bytes,
) -> None:
    fuzz_one(data)


@given(nesting=st.integers(1, 400), token=st.sampled_from(["[]", "()", "{}"]))
def test_generated_nesting_remains_bounded(nesting: int, token: str) -> None:
    source = "value = " + token[0] * nesting + "0" + token[1] * nesting
    inventory = fuzz_one(source.encode("ascii"))
    if nesting > 200:
        assert any(issue.kind == "source_error" for issue in inventory.source.issues)


@given(
    alias_count=st.integers(1, 40),
    resource_count=st.integers(1, 12),
    close_cycle=st.booleans(),
)
def test_structured_alias_and_resource_cycles_terminate(
    alias_count: int, resource_count: int, close_cycle: bool
) -> None:
    aliases = [
        f"Alias{index} = Alias{(index + 1) % alias_count}"
        for index in range(alias_count)
    ]
    definitions = ["\n".join(aliases)]
    for index in range(resource_count):
        name = "Client" if index == 0 else f"Resource{index}"
        target = "Client" if index + 1 == resource_count else f"Resource{index + 1}"
        definitions.append(
            f"class {name}(Alias0):\n"
            "    def ping(self) -> int: ...\n"
            + (
                f"    @property\n    def child(self) -> {target}: ...\n"
                if index + 1 < resource_count or close_cycle
                else ""
            )
        )
    inventory = fuzz_one("\n".join(definitions).encode("ascii"))
    assert any(issue.kind == "unresolved_base" for issue in inventory.issues)
    assert len(inventory.source.classes) == resource_count


@given(data=st.binary(max_size=2048))
def test_malformed_sibling_does_not_erase_valid_module(data: bytes) -> None:
    with TemporaryDirectory(prefix="sdk-atlas-fuzz-sibling-") as directory:
        package = Path(directory)
        (package / "__init__.py").write_text(
            "class Client:\n    def ping(self, value: int) -> int: ...\n",
            encoding="utf-8",
        )
        (package / "broken.py").write_bytes(data)
        inventory = resolve_inventory(
            scan_source(package, "sample", max_source_bytes=4096),
            root_classes=("sample.Client",),
            max_paths=64,
            max_depth=8,
        )
    assert any(record.id == "sample.Client" for record in inventory.source.classes)
    assert "client.ping" in {method.client_path for method in inventory.api_methods}


def test_corpus_size_boundary_is_explicit() -> None:
    fuzz_one(b"#" * MAX_FUZZ_BYTES)
    with pytest.raises(ValueError, match="exceeds"):
        fuzz_one(b"#" * (MAX_FUZZ_BYTES + 1))
