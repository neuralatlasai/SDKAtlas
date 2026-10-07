"""Audit exact installed third-party versions without resolving or installing.

The local sdk-atlas distribution is covered by source gates rather than a
package-index advisory lookup. Every other installed distribution is included;
editable third-party dependencies or ambiguous metadata fail before the audit.
"""

from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*")


def _is_editable(distribution: importlib.metadata.Distribution) -> bool:
    document = distribution.read_text("direct_url.json")
    if document is None:
        return False
    payload: object = json.loads(document)
    if not isinstance(payload, dict):
        raise ValueError("direct_url.json must contain a JSON object")
    directory = payload.get("dir_info", {})
    if not isinstance(directory, dict):
        raise ValueError("direct_url.json dir_info must contain a JSON object")
    return directory.get("editable") is True


def collect_requirements(
    distributions: Iterable[importlib.metadata.Distribution],
) -> tuple[str, ...]:
    """Return deterministic exact pins for installed third-party distributions.

    Args:
        distributions: Installed package metadata to inspect without imports.

    Returns:
        Sorted canonical ``name==version`` pins, excluding only ``sdk-atlas``.
        Identical duplicate metadata records produce a single pin.

    Raises:
        ValueError: Metadata is invalid, a third-party dependency is editable,
            multiple versions of the same name exist, or no dependencies exist.

    This performs O(n) collection and O(k log k) ordering of k distinct names.
    It does not resolve dependencies, install packages, or access the network.
    """
    versions: dict[str, str] = {}
    for distribution in distributions:
        name = distribution.metadata["Name"] or ""
        if not _NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid installed distribution name: {name!r}")
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical == "sdk-atlas":
            continue
        if _is_editable(distribution):
            raise ValueError(f"third-party dependency {name!r} is editable")
        version = distribution.version
        if not _VERSION_PATTERN.fullmatch(version):
            raise ValueError(f"invalid installed version for {name!r}: {version!r}")
        if canonical in versions and versions[canonical] != version:
            raise ValueError(f"multiple installed versions of {name!r}")
        versions[canonical] = version
    if not versions:
        raise ValueError("no third-party distributions found; install .[dev] first")
    return tuple(f"{name}=={versions[name]}" for name in sorted(versions))


def main() -> int:
    """Audit installed third-party pins and return the auditor's exit status.

    The temporary requirements snapshot is removed on exit and is not a
    dependency lockfile. Only the advisory lookup uses the network. Metadata,
    process-start, and timeout failures return one with their diagnostic context.
    """
    try:
        requirements = collect_requirements(importlib.metadata.distributions())
        with tempfile.TemporaryDirectory(prefix="sdk-atlas-audit-") as temporary:
            snapshot = Path(temporary) / "requirements.txt"
            snapshot.write_text("\n".join(requirements) + "\n", encoding="utf-8")
            print(
                f"Auditing {len(requirements)} exact installed third-party versions; "
                "local sdk-atlas source is checked by the project gates.",
                flush=True,
            )
            # Strict requirements mode avoids pip-audit's treatment of skipped
            # editable installs as collection errors. No dependency is omitted
            # apart from this project; unknown advisory versions remain failures.
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip_audit",
                    "--strict",
                    "--no-deps",
                    "--disable-pip",
                    "--requirement",
                    str(snapshot),
                ],
                check=False,
                timeout=600,
            )
            return result.returncode
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"Dependency audit could not complete: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
