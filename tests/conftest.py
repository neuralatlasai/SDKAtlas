"""Share isolated source-tree creation across package behavior tests."""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest


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
