# Contributing

Use Python 3.11 or later and work from the `SDKAtlas` repository directory. Read
[standards.md](standards.md) before changing source or public behavior.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# POSIX shells: . .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/check.py
```

`scripts/check.py` verifies formatting, lint, strict types, deterministic tests,
source and wheel builds, isolated wheel installation, the console entry point,
and installed dependency advisories. It measures branch coverage and enforces
independent total and per-module statement/branch floors. The advisory check
requires network access.
`--skip-security` is available for offline development; it does not satisfy the
complete quality gate. CI runs dependency auditing as a separate required job.
Run `python scripts/audit_dependencies.py` to repeat that job alone. It audits
exact installed third-party versions without package installation or resolution;
the local project is checked by source gates. Editable third-party packages and
unavailable advisory metadata fail the audit. Its temporary version snapshot is
not a checked-in dependency lockfile.

Use an isolated project environment rather than a shared interpreter; CI creates
its own virtual environment for the same reason. Python 3.11's `venv` may seed
`setuptools`, which is not required by the Hatchling backend. If a seeded tool
causes an advisory finding, upgrade it to a fixed version or remove it from this
isolated environment after confirming no declared dependency needs it. Never
exclude an affected third-party dependency from the audit to hide a finding.

Add tests outside `src/` and test the installed package. Keep synthetic SDK
fixtures independent of network access, API credentials, SDK version churn, and
the developer's environment. Every defect correction needs a regression test.
Update API documentation, examples, and `CHANGELOG.md` with observable changes.
Read [testing contracts](docs/testing.md) for the separate mutation, performance,
pinned SDK, and live-latest gates. Install `.[dev,mutation]` to run the Cosmic Ray
campaign. Reviewed small golden fixtures are permitted test oracles; changing
them requires an intentional semantic diff, never automatic acceptance in CI.

The formatter's 88-column profile is the documented line-width exception allowed
by standards section 5. Python source targets Python 3.11 syntax. Suppressions
must be narrow and explain the technical reason in a nearby comment.

Pull requests must explain the concrete behavior, compatibility impact, and
validation result. Review correctness, security boundaries, resource cleanup,
bounded input handling, and evidence quality. SDK Atlas reports static evidence;
do not claim that a helper or a guessed route is a verified REST endpoint.

Dependency ranges are declared in `pyproject.toml`; no dependency lock is checked
in. Record exact tool versions when investigating a gate regression. Fresh
installs can resolve newer releases within the declared ranges; this is not a
claim of a fully locked build environment. See [release.md](docs/release.md)
before creating a release and [governance.md](docs/governance.md) before enabling
hosted repository enforcement or external distribution.
