"""Share isolated source-tree creation across package behavior tests."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from hypothesis import settings

# Reproducible generated cases are mandatory locally and in CI. Every example
# owns its filesystem lifetime; the deterministic suite uses no external service.
settings.register_profile(
    "ci", max_examples=75, deadline=None, derandomize=True, database=None
)
settings.load_profile("ci")


@pytest.fixture
def make_package(tmp_path: Path) -> Callable[[Mapping[str, str]], Path]:
    """Create a synthetic package without importing or executing its files."""

    def create(files: Mapping[str, str]) -> Path:
        root = tmp_path / "sample"
        root.mkdir(exist_ok=True)
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return root

    return create
