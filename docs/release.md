# Build and release procedure

The distribution is `sdk-atlas`; the import package is `sdk_atlas`; the console
entry point is `sdk-atlas`. Python 3.11 is the minimum supported runtime. Public
APIs and export schemas are provisional during `0.x`; document compatibility
changes before changing the package version.

Before release, resolve the license and hosted governance controls in
[governance.md](governance.md). Start from reviewed, version-controlled source
with all required checks passing. Update the authoritative version in
`pyproject.toml` and move the corresponding changelog entry out of Unreleased.

```bash
python -m pip install -e ".[dev]"
python scripts/check.py
python -m build
```

The check script builds both an sdist and a wheel, then installs the wheel into
a separate temporary virtual environment with no runtime dependencies. Its smoke
checks validate import metadata, both entry points, and an offline inventory of
the project's example. Hatchling's reproducible build mode is enabled; the script uses
`SOURCE_DATE_EPOCH=315532800` by default for stable archive timestamps. Callers
may supply a reviewed source commit's timestamp.
Identical source and toolchain inputs are required when comparing artifacts.
Dependency ranges are not a lockfile, so record the exact build/tool versions in
the release record when exact reproduction is required.

The release workflow is manually triggered. It validates the requested version
against package metadata, runs all gates, and retains the artifacts for review.
It has no package-index credentials and does not publish. A future publication
step must use an owner-configured protected environment and a narrowly scoped
trusted publishing identity; it must consume the validated artifacts, not rebuild
from a different revision.

Never replace a published artifact. Correct a defect with a new version and
document the migration or rollback recommendation. SDK Atlas has no deployed
service state; consumers recover by installing a previously reviewed version and
regenerating inventory into a fresh output directory.
