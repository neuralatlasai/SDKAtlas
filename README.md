# SDK Atlas — inspect any Python package or module

SDK Atlas creates a static inventory from installed Python source, a checkout,
or an isolated downloaded wheel. Use it for numerical libraries, machine-learning
frameworks, API clients, agent libraries, or individual modules. Package names,
resources, methods, and endpoints are discovered from source. There is no vendor
catalogue or default target: `--package` is required.

Python **3.11 or later** is required. Runtime dependencies: **none**.

## Install and run

Run these commands from this directory:

```bash
python -m pip install -e .
sdk-atlas --package numpy --out ./numpy_inventory --print-summary
sdk-atlas --package transformers --out ./transformers_inventory
sdk-atlas --package vllm --out ./vllm_inventory
sdk-atlas --package anthropic --out ./anthropic_inventory
sdk-atlas --package openai --out ./openai_inventory
sdk-atlas --package agents --distribution openai-agents --out ./agents_inventory
```

Each target must exist in the interpreter environment used to run SDK Atlas, or
you must provide `--source` or `--latest`. Import and distribution names can differ;
`--distribution` supplies the distribution name for metadata and wheel installation.
No credentials or working target dependencies are required.

Without installing SDK Atlas, set `PYTHONPATH` to `src`:

```powershell
$env:PYTHONPATH = "src"
python -m sdk_atlas --package numpy --out ./numpy_inventory
```

## What is discovered

- Python `.py` modules, `.pyi` type stubs, and native `.pyd`/`.so` module presence.
- Module-level functions, overloads, async definitions, parameters, annotations,
  defaults, decorators, and docstrings.
- Public imports, re-exports, aliases, constants, and annotated variables.
- Classes, inheritance, methods, properties, and public instance assignments.
- Constructors, Python protocol methods, and class-local callable aliases.
- Declared response-model fields and source-derived expected return structures.
- Literal functional `TypedDict`/`NamedTuple` declarations and assignment type aliases.
- Resolved inherited members and descriptor/resource transitions.
- Reachable paths such as `client.<discovered_attribute>.<discovered_method>`.
- Path-bearing calls, literal/formatted route evidence, and HTTP operation hints.

Functions and class definitions are retained independently of whether a library
has a client/resource graph. `functions.csv` serves numerical and utility modules;
`methods.csv` contains every discovered public, constructor, protocol, and property
definition, including variants and class-local aliases. `api_methods.csv` adds
resolved class-based paths. Ordinary private helpers are excluded unless exposed
through a public class-local alias. Module members retain re-export
names even when their implementation is native, external, or unresolved. SQLite
retains all public method definitions.

## Scan a source tree or a single module

```bash
sdk-atlas --package your_package --source /path/to/repository --out ./inventory
sdk-atlas --package your_package --source /path/to/repository/src/your_package --out ./inventory
sdk-atlas --package your_module --source /path/to/your_module.py --out ./inventory
sdk-atlas --package vendor.module --source /path/to/module.pyi --out ./inventory
```

Both `repo/<package>/` and `repo/src/<package>/` layouts support dotted imports.
Explicit `.py`, `.pyi`, and native extension files are accepted. Namespace packages
are scanned within the selected filesystem root.

## Include dependency source explicitly

Only the target is scanned by default. Add support source when an inherited method,
resource, or response model is defined in another package:

```bash
sdk-atlas --package your_package --dependency dependency_package --out ./inventory
sdk-atlas --package your_package --source /path/to/project --dependency-source dependency_package=/path/to/dependency --out ./inventory
```

Both flags are repeatable. `--dependency` reads an installed import package;
`--dependency-source IMPORT_NAME=PATH` reads a repository, package directory,
module, or stub file. Quote the mapping when its path contains spaces. Dependencies
are never imported, installed automatically, or followed recursively. `--latest`
downloads only the primary target; explicitly requested dependencies still come
from installed or local source.

Duplicate or overlapping package namespaces are rejected before publication.
Merged source tables include support records, while the primary package/version
remain unchanged. `support_sources` in the summary and SQLite metadata records
each support package's root and version. File locations remain relative to the
source root of their defining package.

## Scan the latest compatible wheel

```bash
sdk-atlas --package numpy --latest --out ./numpy_latest_inventory
sdk-atlas --package agents --distribution openai-agents --latest --out ./agents_inventory
```

`--latest` runs `python -m pip install --no-deps --only-binary=:all: --target
<temporary-directory> <distribution>`, analyzes files, and removes the temporary
target. The active environment is preserved. This mode requires pip, network
access, and a wheel compatible with the interpreter/platform. Use `--source` for
packages whose wheels are unavailable on your platform.

## Output

Each successful export writes these 14 files:

```text
modules.csv             module locations, source kinds, imports and exports
functions.csv           standalone function definitions and parameter metadata
module_members.csv      public bindings, re-exports, aliases and constants
classes.csv             all discovered classes, bases and decorators
class_fields.csv        declared public fields, defaults and requiredness
methods.csv             all discovered method definitions, arguments and outputs
api_methods.csv         resolved public class-method paths and definitions
parameters.csv          normalized class-method arguments
resource_graph.csv      resolved descriptor/instance-attribute transitions
transport_evidence.csv  class-method call and route evidence
dynamic_members.csv     public instance-attribute assignments
inventory.jsonl         one detailed record per resolved API method path
inventory.sqlite        normalized source, graph and expected-output tables
summary.json            schema version, provenance, counts and diagnostics
```

Function parameters and transport details are included in `functions.csv` and
normalized into SQLite tables. `methods.csv`, `api_methods.csv`, and
`functions.csv` contain an `expected_output` JSON cell. CSV collection cells
contain JSON. Files use UTF-8
and deterministic source/path order. Existing owned files are replaced with
rollback on failure; unrelated files are preserved. Existing-directory publication
is not isolated from concurrent readers.

Expected-output JSON cells can exceed Python CSV's default field-size limit.
Readers can set an explicit bound, for example
`csv.field_size_limit(16 * 1024 * 1024)`, or query SQLite. The exported JSON is
retained in full rather than truncated for a spreadsheet cell.

Choose the table for the question being answered:

| Question | Read |
| --- | --- |
| Which class callables exist, including those without client paths? | `methods.csv`; `parameters` contains caller arguments and `declared_parameters` retains receivers. |
| How can a selected root reach a method? | `api_methods.csv` or `inventory.jsonl`; several paths can reference the same `method_id`. |
| Which standalone functions exist? | `functions.csv`; these do not require a class root. |
| What fields and types can a callable return? | Its `expected_output`, with declaration evidence in `class_fields.csv`. |
| How can records be joined without parsing JSON cells? | `inventory.sqlite`; its 28 tables normalize declarations, parameters, paths, evidence, and output-model links. |

The [output schema reference](docs/output-schema.md) lists every column, JSON
object, SQLite table, join, status, and encoding rule. The
[coverage and performance review](docs/review.md) records measured coverage and
profiling evidence.

Try the bundled source fixture without installing or importing it:

```bash
sdk-atlas --package demo_sdk --source examples --out ./inventory --print-summary
```

Its `methods.csv` retains the `Things` constructor, `Things.retrieve`, and the
`Client.things` property. `api_methods.csv` exposes `client.things.retrieve`;
the caller supplies `thing_id: str`. Its `dict[str, str]` return annotation has
status `type_only`: the annotation is known, but it names no source model with
fields. `functions.csv` independently retains `normalize_identifier`.

```python
import csv
import json
from pathlib import Path

csv.field_size_limit(16 * 1024 * 1024)
with Path("inventory/api_methods.csv").open(encoding="utf-8", newline="") as stream:
    for row in csv.DictReader(stream):
        arguments = json.loads(row["parameters"])
        output = json.loads(row["expected_output"])
        print(row["client_path"], [arg["name"] for arg in arguments], output["status"])
# client.things.retrieve ['thing_id'] type_only
```

`method_kind` distinguishes `transport`, `delegating_helper`, and `sdk_helper`.
Path-bearing calls are evidence, rather than guarantees of an HTTP request.
HTTP verbs, Python descriptor conventions, typing wrappers, file suffixes, and
output column names describe language/protocol/schema semantics; they do not
restrict which vendor, package, resource, or callable can be inventoried.

## Caller arguments and expected outputs

Parameters preserve names, positional-only/keyword-only/variadic kinds, annotation
text, default expressions, and ordinary parameter requiredness. Variadic arguments
are marked not required. Methods also record `binding` as `instance`, `class`, or
`static` for recognized Python decorators. The first declared positional parameter
of instance/class methods is marked
`implicit`, even if it is named `this` or `kind` instead of `self` or `cls`.
Static methods have no implicit receiver; a static argument named `self` is still
a caller argument. Variadic-only definitions retain their variadic declaration.

`parameters.csv`, SQLite parameter tables, and JSONL `parameters` preserve the
complete declaration, including implicit receivers. The `parameters` cell in
`methods.csv` and `api_methods.csv`, and JSONL `call_parameters`, omit implicit
receivers. `methods.csv` also preserves `declared_parameters` and associates
available paths through `client_paths`; an empty path list does not hide a method.
Source-derived declaration signatures retain the receiver; formatting is
normalized by the Python AST rather than rewritten into a bound call signature.

Expected outputs describe static type/model evidence, never an executed response:

Assignment aliases, including generic/union expressions and imported aliases,
are expanded within a fixed nesting budget. Alias cycles and dynamic annotation
expressions remain incomplete. `Literal` values, `Annotated` metadata, and
`Callable`/`TypeGuard`/`TypeIs` arguments are not response models. `Self` and
unsubstituted type parameters retain an unknown shape.

| Field | Meaning |
| --- | --- |
| `callable_id` | Method/function definition ID; joins `methods.csv` or `functions.csv`. |
| `annotation` | Source return annotation, including container/union structure. |
| `model_ids` | Resolved source classes mentioned by the annotation or inferred method return. |
| `fields` | Objects with `model_id`, `class_id`, `name`, `annotation`, `default`, `requiredness`, and `inherited`; `model_id` is the returned model and `class_id` is the declaring class. |
| `status` | Resolution state listed below. |
| `unresolved` | Missing references, incomplete ancestry, or traversal/field-limit markers. |

| Status | Meaning |
| --- | --- |
| `resolved` | Return model classes resolved without known gaps in the analyzed evidence. |
| `inferred` | An unannotated method returns a recognized class reference/constructor, without known gaps. |
| `type_only` | An annotation describes a scalar, container, or typing form without a resolved response model. |
| `partial` | A model resolved, but some references, ancestry, dynamic type information, or fields remain incomplete. |
| `unresolved` | Return references could not be resolved to supplied source or recognized type facilities. |
| `unannotated` | No return annotation or supported method-return inference is available. |
| `unknown` | Shape is unspecified/dynamic, such as `Any`, or an older manual inventory lacks analysis. |

Field `requiredness` is `required`, `optional`, or `unknown` from source syntax.
Recognized `TypedDict` `total=False`, `Required`, and `NotRequired` declarations
are retained, including nested `ReadOnly` qualifiers and literal functional
definitions. Qualifiers require typing import provenance. A literal/default
expression can indicate optionality; calls such
as default factories remain unknown. Inherited fields follow the resolved class
order and child overrides. `ClassVar` declarations stay in `class_fields.csv` but
are excluded from returned instance fields. Public properties and instance
assignments can contribute fields with unknown requiredness.

Requiredness is not a promise about a runtime constructor or wire payload.
Validators, serializers, aliases, dynamic defaults, and nested model contents are
not executed or fully expanded. Field annotations and default expressions remain
text. Output inference covers all discovered method and function definitions,
independently of selected graph roots or traversal depth. Manually constructed
older inventories without output records retain an explicit `unknown` fallback.

SQLite stores output JSON in `method_outputs` and `function_outputs`, and model
links in `method_output_models` and `function_output_models`. Fields remain nested
in `output_json`; declared field evidence is also available in `class_fields`.
Existing `methods`, `parameters`, `functions`, and `function_parameters` tables
retain complete declaration metadata.

## Static-analysis limits and configuration

Target code is **never imported or executed**. Compiled implementations, runtime
monkey-patching, metaclass-generated methods, dynamic `__getattr__` exports, and
custom import hooks cannot be fully reconstructed statically. Native signatures
are recovered where readable `.pyi` stubs exist; remaining native modules are
reported explicitly. Built-in/frozen modules and zip installations without a
filesystem source tree require an explicit source/stub file.

Properties recognize Python's `property` and common cached/class property
conventions. Custom descriptors remain definitions but may lack graph transitions.
Conditional definitions are static possibilities rather than executed branches.
Wildcard re-exports remain binding evidence. Syntax newer than the running parser
produces diagnostics.

`--root package.module.Class` selects a graph root; repeat for several roots.
Defaults prefer public package class exports, then structural roots and public
classes within the primary target. A support class used only as a base does not
automatically become a root. Explicit target `__all__` exports or `--root` can
select a supplied support class deliberately. Each root uses display prefix
`client`; `root_class` disambiguates roots. Module-level functions do not depend
on root selection.
Graph depth bounds paths, not declaration inventory. `methods.csv` and SQLite
retain definitions even when no path reaches their class. Summary counts expose
methods with/without paths, output analysis, and constructor/protocol definitions.

Inheritance diagnostics distinguish different boundaries:

| Diagnostic | Meaning |
| --- | --- |
| `external_base` | An import resolves outside the supplied source namespaces. Include its source with a dependency flag to inspect inherited members. |
| `unresolved_base` | A base is unbound, missing within supplied source, cyclic through aliases, or otherwise not statically resolvable. |

Recognized built-in bases such as `object` and `Exception` do not cause these
diagnostics. Their native methods are not reconstructed. Local bindings can
shadow built-in names; an unresolved shadow is not treated as the built-in type.

Defaults are 32 graph/inheritance/alias levels, 100,000 graph paths, 100,000 source
files, and 8 MiB per source file. Override with `--max-depth`, `--max-paths`,
`--max-files`, and `--max-source-bytes`. `--max-paths` also bounds cumulative
inheritance expansion and effective-member resolution. Each AST is limited to
500,000 nodes. Source file/byte limits apply separately to each explicitly supplied
package. Return-field expansion also uses `--max-paths`; return-annotation
traversal has an additional fixed nesting bound.
Distinct union field projections are cached and have a separate field-reference
budget. Repeated methods returning one model share its immutable field tuple.
Bounds and unresolved evidence appear in `summary.json` and stderr; output with
diagnostics is intentionally a partial inventory. Configuration comes from
explicit CLI arguments and documented defaults.
Reads allocate against the opened file's verified size rather than the configured
maximum. Size and modification-time checks before and after reading report
`source_changed` for detected concurrent edits; they do not create an atomic
snapshot of the entire source tree. AST node counting is iterative and applies
the same fixed node limit without recursive counting.

## Development and compatibility

```bash
python -m pip install -e ".[dev]"
python scripts/check.py --artifacts dist
```

The check runs formatting, lint, strict typing, deterministic tests, dependency
auditing, package builds, and a clean installed-wheel smoke scan.
`python scripts/check.py --skip-security` runs offline checks but does not satisfy
the complete security gate. Generated inventories and environments are ignored.

Version 0.1 is experimental. Output includes `schema_version`; incompatible public
interface/schema changes require a version change and changelog entry. See
[output schema](docs/output-schema.md),
[coverage and performance review](docs/review.md),
[architecture](docs/architecture.md), [contributing](CONTRIBUTING.md),
[security](SECURITY.md), and [release controls](docs/release.md). Hosted protection,
reviewer identities, and license selection require owner configuration as recorded
in [governance](docs/governance.md).
