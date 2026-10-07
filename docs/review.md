# Method coverage, quality, and performance review

Reviewed on 2026-10-07 against OpenAI 3.26.0, Agents 0.23.1, and NumPy 2.5.3.
Counts describe declarations in the selected source tree, including overloads,
internal modules, and shipped test classes. They are not counts of unique public
runtime APIs. The scanner does not execute the target.

## Confirmed coverage defects and corrections

Previously, return analysis used only method definitions reachable from selected
client roots. CSV exposed those paths while other method declarations were
available only in SQLite. Constructors and protocol methods were excluded by the
private-name rule. Class-local callable aliases could also disappear.

| Package | Previous definitions | Previously analyzed methods | Current definitions and analyzed methods |
| --- | ---: | ---: | ---: |
| OpenAI | 2,875 | 957 | 3,758 / 3,758 |
| Agents | 1,436 | 201 | 1,785 / 1,785 |
| NumPy | 8,196 | 436 | 10,976 / 10,976 |

`methods.csv` now exposes every discovered method variant with declaration and
caller parameters, binding, docstring, source location, expected output, and
associated client paths. Constructors, protocol methods, properties, and supported
class-local aliases are retained. Root selection and path depth no longer filter
method return analysis. Module protocol hooks are also retained in `functions.csv`.
`api_methods.csv` remains the graph-path view; absence there does not imply an
absent declaration. Ordinary private helpers remain excluded unless exposed by a
supported public alias.

Parameter extraction preserves positional-only, keyword-only, variadic, default,
annotation, and overload evidence. Implicit receivers are explicit metadata and
are removed from caller argument lists. Tests cover renamed receivers, static and
class methods, and constructor arguments.

Return analysis expands source assignment aliases, imported aliases, container
and union forms, and literal functional TypedDict/NamedTuple declarations.
Inherited field overrides and typing qualifiers are retained. Literal values,
annotation metadata, predicate arguments, and Callable signatures are excluded
from response fields. Dynamic expressions, Self, type parameters, missing bases,
and truncated local ancestry remain explicitly incomplete.

## Data quality observed in source

| Package | Methods with docstrings | Methods with return annotations | Annotated caller parameters |
| --- | ---: | ---: | ---: |
| OpenAI | 2,007 / 3,758 | 3,756 / 3,758 | 11,309 / 11,309 |
| Agents | 609 / 1,785 | 1,667 / 1,785 | 2,946 / 2,980 |
| NumPy | 557 / 10,976 | 3,848 / 10,976 | 6,653 / 9,456 |

Missing source documentation and types are retained as missing evidence rather
than generated claims. Nested field annotations remain text; arbitrary validator,
serializer, decorator, native, and metaclass behavior is not reconstructed.
Class aliases involving arbitrary expressions and dynamic generated exports may
still need source/stubs or further static-analysis support.

Built-in bases no longer produce false unresolved diagnostics. Imported bases
outside supplied source use `external_base`, with explicit dependency-source
options. OpenAI has zero `unresolved_base` reports after functional typing support.
Agents retains one dynamic StreamableHTTPTransport base; NumPy retains one
conditional `_ndptr_base`. These are kept visible. External inheritance still
requires explicitly supplied source; it is not silently considered complete.

## Performance and complexity

The exporter uses a bounded 1,024-definition serialization cache. Effective model
fields and union projections are cached; repeated methods returning one model
share its immutable field tuple. A regression with 200 methods and 200 fields
verifies sharing instead of retaining 40,000 duplicate tuple positions. Distinct
union projections have a separate field-reference budget. Default scans avoid an
unnecessary source-merge copy when no dependencies were requested.

Source traversal is linear in source/AST/emitted evidence, apart from bounded
path sorting. Indexed C3 merging uses O(M log B) merging for M expanded entries
and B parent sequences. Field expansion and graph work are bounded; serialization
still scales with output bytes. SQLite adds B-tree index maintenance. No SDK name
catalogues or SDK-specific execution paths are used.

Run a local benchmark with:

```bash
python scripts/benchmark.py --package openai --source ../.venv/Lib/site-packages/openai --warmup 1 --repeats 3
```

On this Windows AMD64 / CPython 3.13.13 environment, three measured runs after one
warmup produced the following medians for 1,922 OpenAI modules and 160,326,731
export bytes:

| Stage | Before read/traversal optimization, seconds | After, seconds |
| --- | ---: | ---: |
| Parse and extract | 5.702 | 2.570 |
| Resolve graph and expected outputs | 0.361 | 0.316 |
| Export all 14 files | 3.183 | 3.257 |
| Total measured stages | 9.250 | 6.143 |

Timing excludes locating source, cleanup, and benchmark bookkeeping. Results are
local measurements rather than a cross-machine guarantee. The same-workload
comparison reduced median total latency by 33.6% (1.51x throughput equivalent).
All 14 exported files matched their previous SHA-256 hashes, including SQLite.
Exporting is now the largest stage. JSON benchmark output includes nanosecond
samples, counts, platform, and rates.

Profiling identified oversized read-buffer allocation and layered AST counting
as avoidable costs. Reads now allocate against opened-file size within the byte
limit; size/mtime checks report detected concurrent modification. The AST guard
uses an iterative LIFO frontier with exactly the standard traversal's node-count
semantics. A 72-byte source with the default 8 MiB limit had traced read peaks of
8,397,866 bytes before and 10,065 bytes after. This is a read-only allocation
measurement, not a whole-scan peak-memory claim. Regression tests enforce a
whole-scan small-file allocation budget, source-change reporting, and exact AST
budget boundaries across multiple syntax forms.

## Validation

Regression tests cover declaration coverage independent of roots, aliases,
arguments, docstrings, return shapes, limits, and publication rollback. The full
quality check includes formatting, lint, strict typing, tests, dependency audit,
wheel/sdist builds, and a clean installed-wheel smoke scan.

The three refreshed real inventories passed SQLite integrity/foreign-key checks.
Their methods.csv identifiers match SQLite declarations, every declaration has an
output-analysis record, and caller parameter lists contain no implicit receiver.
Analysis coverage measures records processed; it does not imply all response
shapes are known.

The subsequent testing-rigor review is addressed by repository-controlled
coverage floors, generated cases and malformed-input fuzzing, a controlled
runtime oracle, all-artifact goldens, scoped mutation and performance budgets,
filesystem adversaries, and automated pinned/latest package CI contracts. See
[testing contracts](testing.md) for exact thresholds, reproduction commands,
evidence scope, and remaining limits. Passing these checks does not assign a
review score or establish correctness for arbitrary runtime behavior.
