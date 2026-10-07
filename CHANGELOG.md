# Changelog

## 0.1.0 — Unreleased

- Add a Python 3.11+ static Python module/package inventory and `sdk-atlas` command.
- Require an explicit import-package name without a vendor-specific default.
- Inventory module-level functions, imports, aliases, constants, and available
  type stubs; identify native modules without claiming to recover opaque symbols.
- Discover client/resource surfaces and transport evidence from SDK source
  without an endpoint catalog or API credentials.
- Add opt-in installed/local support sources with `--dependency` and
  `--dependency-source`, immutable merging, and source provenance.
- Distinguish external imported bases, unresolved local bases, and recognized
  built-ins; exclude support-only classes from automatic fallback roots.
- Preserve method binding and implicit receivers separately from caller arguments.
- Infer bounded expected return-model fields, inheritance, source requiredness,
  and explicit resolution statuses without evaluating target code.
- Expand assignment type aliases and literal functional typing declarations;
  distinguish predicate types, runtime annotation expressions, and unknown shapes.
- Reuse bounded method serialization and immutable response-field projections;
  provide reproducible nanosecond stage benchmarks without a target import.
- Size source reads against opened files, detect source changes, and count AST
  nodes iteratively; verify identical exports and measured local latency gains.
- Export CSV, JSON Lines, SQLite, and summary data, including `modules.csv`,
  `functions.csv`, and `module_members.csv` for module-level surfaces; add
  `class_fields.csv` and `methods.csv` for 14 total artifacts and SQLite
  expected-output tables.
- Retain constructor/protocol declarations and class-local callable aliases;
  analyze return structures for every discovered definition independently of
  graph reachability, with explicit coverage counts.
- Provide installed-package, source-tree, and isolated latest-distribution scan
  modes.
- Add typed source layout, deterministic tests, quality gates, and build checks.
- Enforce repository LF endings across platforms for formatter/build consistency.
- Enforce independent statement/branch coverage and per-module floors; retain
  coverage evidence for the supported interpreter/platform CI matrix.
- Add deterministic Hypothesis properties, malformed-source fuzz replay,
  controlled runtime differential checks, and reviewed all-artifact goldens.
- Gate selected production predicate mutations with Cosmic Ray; gate synthetic
  latency, allocation, throughput, scaling, and complete-output budgets.
- Automate pinned OpenAI, Agents, and NumPy contracts and live `--latest` scans;
  test filesystem links, traversal attempts, and publication boundary conflicts.

The initial `0.x` API and export schemas may change between minor releases;
observable changes must be documented here before release.
