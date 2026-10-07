# Output schema reference

This reference describes the current `schema_version: 1` export from
`sdk_atlas.export`. Read `summary.json` first. Its `version` is the **scanned
distribution's version**, not the SDK Atlas version; `sdk-atlas --version` reports
the tool version. Unknown distribution versions are the string `"unknown"`.

The initial SDK Atlas 0.x interface is experimental. Consumers should check the
schema version, address CSV columns by header, and tolerate additional evidence
values where appropriate. Incompatible schema/public-interface changes require a
reviewed version change and changelog entry. CSV files do not carry a separate
schema-version column; SQLite stores it in `metadata` as text.

## Artifact scope

Every successful export creates these 14 owned files, including header-only CSV
files when a category has no records:

| File | Records and purpose |
| --- | --- |
| `modules.csv` | Discovered modules, source kinds, import bindings, and explicit exports. |
| `functions.csv` | Retained module-level function variants, declaration arguments, and expected outputs. |
| `module_members.csv` | Public module bindings: imports, aliases, functions, classes, constants, variables, and wildcard evidence. |
| `classes.csv` | Discovered class declarations, including bases and decorators. |
| `class_fields.csv` | Declared public fields and their source requiredness/default evidence. |
| `methods.csv` | Every retained method variant, including constructors/protocol methods, properties, and supported public aliases; paths are optional. |
| `api_methods.csv` | One row per resolved graph path and method variant; a definition can have several rows or none. |
| `parameters.csv` | Complete retained method declaration parameters, including implicit receivers. |
| `resource_graph.csv` | Resolved property/instance-member transitions between classes and paths. |
| `transport_evidence.csv` | Path-bearing calls inside retained class methods. |
| `dynamic_members.csv` | Public instance attributes assigned in analyzed method bodies. |
| `inventory.jsonl` | Detailed resolved API-path records; it is not the complete declaration inventory. |
| `inventory.sqlite` | Normalized declaration, graph, parameter, evidence, and output tables. |
| `summary.json` | Schema, primary/support provenance, counts, and diagnostics. |

"Retained" follows the static scanner's naming policy: public and double-underscore
constructor/protocol definitions are kept, including supported class-local public
aliases. Ordinary private helpers are excluded unless exposed by such an alias.
Classes and modules may come from internal or shipped test modules; declaration
counts are not counts of unique public runtime APIs. Overloads and source/stub
variants can produce multiple records for one callable name.

Explicit support sources contribute declaration records and output analysis.
Graph-root and traversal settings affect `api_methods.csv`/JSONL coverage, not the
presence of retained method/function definitions or their return analysis.

## Encoding and identity conventions

- Text files use UTF-8. CSV has a header and standard CSV quoting, with LF record
  terminators; quoted docstrings can contain embedded newlines. Use a CSV parser.
- CSV scalar booleans are `true`/`false`; integer cells are decimal text. JSON and
  JSONL use native JSON booleans/numbers. SQLite booleans are INTEGER `0`/`1`.
- An absent annotation, default expression, docstring, or optional textual
  reference is normally `""`, not JSON `null`. A Python default of `None` is the
  text `"None"`; it differs from an absent default. Empty collections are `[]`.
  Missing output fields do not prove that a function returns nothing.
- Structured CSV cells contain JSON. After CSV decoding, apply `json.loads`
  once to those cells; do not evaluate annotation/default/source expressions.
- Source lines are one-based. Diagnostic line `0` means no particular line is
  attached. Definition `end_line` is inclusive.
- Definition `file` values are relative to their original package root, using
  `/` within directories; a scanned single file uses its basename. Use the
  defining module/class namespace and `support_sources` to choose the root.
  Issue records have no module key, so equal relative filenames across support
  roots may require contextual correlation.
- IDs are join keys within an inventory, not permanent cross-version IDs.
  Class IDs are qualified names; callable/member IDs include source-location or
  variant evidence. API/edge IDs are local generated identifiers. Source edits
  and changed discovery order can change IDs.
- `signature`, `annotation`, `default`, expressions, and routes are source-derived
  text, often normalized by the Python AST. They are not evaluated values or
  guaranteed verbatim source slices.

Large output cells may exceed Python CSV's default field limit. Set a deliberate
reader bound, such as `csv.field_size_limit(16 * 1024 * 1024)`, or query SQLite.
Choose the bound for your inventory; exports are not truncated to spreadsheet
cell limits.

The column lists below are in file order. Types are logical types: `string`,
`integer`, `boolean`, or `json<T>` for a JSON-encoded CSV cell. Named object
types are defined after the column lists.

## Exact CSV columns

### `modules.csv`

```text
name: string, file: string, is_package: boolean, source_kind: string,
imports: json<ImportBinding[]>, exports: json<string[]>
```

`name` is the absolute dotted module name. `source_kind` contains `python`,
`stub`, `native`, or a `+`-joined combination when evidence comes from several
files. `exports` contains statically collected `__all__` entries; an empty array
does not distinguish absent `__all__` from an explicitly empty declaration.
Import bindings do not prove that the imported module was supplied or executable.

### `functions.csv`

```text
id: string, module: string, name: string, signature: string,
return_annotation: string, docstring: string, file: string,
line: integer, end_line: integer, is_async: boolean, is_overload: boolean,
parameters: json<Parameter[]>, decorators: json<string[]>,
transport: json<TransportEvidence[]>, expected_output: json<CallableOutput>
```

Each row is a module-level definition variant. Parameters are complete declaration
parameters; there is no implicit instance/class receiver for a module function.
Function transport evidence is nested here and normalized in SQLite
`function_transport`; it is not in the class-method transport CSV.

### `module_members.csv`

```text
id: string, module: string, name: string, kind: string,
expression: string, annotation: string, file: string, line: integer
```

Current `kind` values include `import`, `wildcard_import`, `alias`, `constant`,
`variable`, `function`, and `class`. These describe binding evidence, not the
runtime type of the resulting value. Re-export names can remain visible even
when an implementation is external or native. Wildcards are not executed.

### `classes.csv`

```text
id: string, module: string, name: string, qualname: string,
docstring: string, file: string, line: integer,
bases: json<string[]>, decorators: json<string[]>
```

`qualname` includes enclosing class names; `id` adds the module prefix. `bases`
and decorators are source expressions, not serialized Python class objects.

### `class_fields.csv`

```text
id: string, class_id: string, name: string, annotation: string,
default: string, requiredness: string, file: string, line: integer, kind: string
```

These are direct field declarations, not fields projected onto every subclass.
`kind` is `data` or `class_variable`. Recognized literal functional
TypedDict/NamedTuple declarations can also contribute classes and fields.
Dynamic assignments and properties are represented elsewhere and can contribute
to expected-output fields without becoming declared `class_fields` rows.

### `methods.csv`

```text
id: string, class_id: string, name: string, signature: string,
return_annotation: string, docstring: string, file: string,
line: integer, end_line: integer, is_async: boolean, is_property: boolean,
is_overload: boolean, binding: string,
parameters: json<Parameter[]>, declared_parameters: json<Parameter[]>,
decorators: json<string[]>, transport: json<TransportEvidence[]>,
expected_output: json<CallableOutput>, client_paths: json<[string, string][]>
```

`id` is the method-definition ID and `class_id` its declaring class. `parameters`
contains caller arguments; `declared_parameters` includes implicit receivers.
`client_paths` consists of `[root_class, client_path]` pairs. An empty list means
no path reached the definition, not that its declaration or output is missing.
Constructors, properties, and protocol methods can be present without API paths.

### `api_methods.csv`

```text
id: string, root_class: string, resource_class: string, defined_in: string,
client_path: string, method_name: string, method_id: string,
method_kind: string, inherited: boolean,
signature: string, return_annotation: string, is_async: boolean,
is_overload: boolean, file: string, line: integer, docstring: string,
binding: string, parameters: json<Parameter[]>,
expected_output: json<CallableOutput>
```

`id` identifies a path record; `method_id` joins `methods.csv.id` or SQLite
`methods.id`. `root_class` is the entry class, `resource_class` the class reached
at this path, and `defined_in` the method's declaring class. `inherited` compares
the reached and declaring classes. Every root uses display prefix `client`;
`root_class` disambiguates identical displayed paths from different roots.

`method_kind` is `transport` when direct path-bearing call evidence exists,
`delegating_helper` for public receiver-call evidence without direct transport,
or `sdk_helper` otherwise. None of these labels verifies a server endpoint.

### `parameters.csv`

```text
method_id: string, class_id: string, method_name: string, position: integer,
name: string, kind: string, annotation: string, default: string,
required: boolean, implicit: boolean
```

Rows are complete method declaration parameters. `position` is zero-based in
declaration order, including an implicit receiver. Module-function parameters
are in `functions.csv` and SQLite `function_parameters`.

### `resource_graph.csv`

```text
id: string, root_class: string, source_class: string, target_class: string,
source_path: string, target_path: string, member: string, kind: string,
file: string, line: integer
```

Each row is a class transition from `source_path` to `target_path` through
`member`. Current kinds are `property` and `dynamic_member`. These are statically
resolved transitions, not observed object construction or network activity.

### `transport_evidence.csv`

```text
id: string, method_id: string, class_id: string, module: string,
file: string, line: integer, call: string, operation: string,
route: string, route_kind: string
```

This contains class-method evidence only; the shared object type below explains
operations/routes and the function-specific naming difference.

### `dynamic_members.csv`

```text
id: string, class_id: string, name: string, expression: string,
annotation: string, file: string, line: integer, target_refs: json<string[]>
```

`expression` is assigned source text. `target_refs` contains candidate class or
annotation references; it is not a list of confirmed runtime attribute types.

## Structured JSON objects

### `Parameter`

| Key | JSON type | Meaning |
| --- | --- | --- |
| `name` | string | Declared parameter name. |
| `kind` | string | `POSITIONAL_ONLY`, `POSITIONAL_OR_KEYWORD`, `VAR_POSITIONAL`, `KEYWORD_ONLY`, or `VAR_KEYWORD`. |
| `annotation` | string | Annotation text, or empty. |
| `default` | string | Default-expression text, or empty when absent. |
| `required` | boolean | Ordinary parameter lacks a default; variadic parameters are false. |
| `implicit` | boolean | Declared instance/class receiver rather than a caller argument. |

`binding` on methods is `instance`, `class`, or `static` for recognized
decorators. The first declared positional receiver can be named `self`, `this`,
`cls`, or another identifier. A static argument named `self` is not implicit.
Variadic-only definitions retain their variadic declaration. Signatures keep
source receiver parameters even when caller-facing argument arrays omit them.

### `CallableOutput` (`expected_output` / SQLite `output_json`)

| Key | JSON type | Meaning |
| --- | --- | --- |
| `callable_id` | string | Owning method/function definition ID, not an API-path ID. |
| `annotation` | string | Source return annotation, including aliases/container/union syntax. |
| `model_ids` | string[] | Resolved source classes referenced by return evidence. |
| `fields` | OutputField[] | Projected public fields for the recognized model classes. |
| `status` | string | Static resolution status below. |
| `unresolved` | string[] | Missing references, incomplete ancestry, or expansion-limit markers. |

`model_ids` is the available model/type reference list; there is no separate
`types` property. The annotation retains scalar/container/union structure.
An empty `fields` array can mean a scalar type, opaque/unknown structure, or a
recognized class with no recoverable public fields. Interpret it with status
and annotation. Fields of nested field types are not recursively expanded.

| Status | Meaning |
| --- | --- |
| `resolved` | Model references resolved without known gaps in the analyzed evidence. |
| `inferred` | An unannotated method supplies a recognized class constructor/reference without known gaps. |
| `type_only` | An annotation describes recognized scalar/container/typing facilities without a response model. |
| `partial` | At least one model resolved, but references, ancestry, shape information, or field projections are incomplete. |
| `unresolved` | Return references remain unresolved without a resolved model. |
| `unannotated` | No annotation or supported method-return inference is available. |
| `unknown` | Shape is unspecified/dynamic, such as `Any`, `Self`, or an unsubstituted parameter; older manual inventories can also lack analysis. |

All retained method and supplied function definitions receive analysis in normal
scanner output, independently of graph roots. Unannotated **module functions**
do not use the method-only constructor/reference inference. Manual inventories
without analysis records are exported with `unknown` and retained annotation.
Statuses report source evidence, not runtime response correctness.

### `OutputField`

| Key | JSON type | Meaning |
| --- | --- | --- |
| `model_id` | string | Returned model onto which this field is projected. |
| `class_id` | string | Class declaring the effective field. |
| `name` | string | Public field/member name. |
| `annotation` | string | Field/property annotation text, possibly empty. |
| `default` | string | Source default/assignment text, possibly empty. |
| `requiredness` | string | `required`, `optional`, or `unknown` from source syntax. |
| `inherited` | boolean | Declaring `class_id` differs from returned `model_id`. |

Child declarations override inherited fields. Recognized typing provenance,
`TypedDict` totality, `Required`/`NotRequired`, and nested `ReadOnly` qualifiers
inform requiredness. Literal/non-call defaults can indicate optionality;
factory calls and dynamic defaults remain unknown. Properties and public
instance assignments can contribute fields with unknown requiredness.
`ClassVar` evidence remains in `class_fields.csv` but is excluded from instance
output fields. Requiredness does not certify constructor requirements, aliases,
validators, serialization rules, or actual wire payloads.

### `ImportBinding`, `TransportEvidence`, and provenance

`ImportBinding` contains four string keys: `name` (local name), `target`
(canonical import or source alias target), `kind` (`import` or `alias`), and
`expression` (preserved alias-expression evidence, otherwise possibly empty).

`TransportEvidence` has these keys/types:

```text
id: string, method_id: string, class_id: string, module: string,
file: string, line: integer, call: string, operation: string,
route: string, route_kind: string
```

`call` is the transport-like symbol. `operation` is a best-effort lower-case HTTP
operation or `unknown`; `route_kind` is `literal`, `formatted`, or `expression`.
`route` is literal/template/source-expression evidence, not a verified request.
For evidence nested in a **function** record, the shared object's `method_id`
contains that function's ID and `class_id` is empty. SQLite renames the owner
column to `function_id` in `function_transport`.

`SourceReference` has string keys `package`, `root`, and `version`. Primary
`source_root` and support `root` can name a directory or a single file. Latest
scan roots record the temporary analysis location, which is removed afterward;
the export does not preserve a copy of the package source.

`ScanIssue` has `kind: string`, `message: string`, `file: string`, and
`line: integer`. Diagnostics describe skipped or incomplete evidence; they are
not automatically proof of an invalid target package.

## `inventory.jsonl`

Each line is one JSON object for one resolved API path. It combines the scalar
method-definition fields listed for `methods.csv` with all API-path fields listed
for `api_methods.csv`, plus:

```text
parameters: Parameter[], call_parameters: Parameter[], decorators: string[],
returns: string[], delegates: string[], transport: TransportEvidence[],
expected_output: CallableOutput
```

`parameters` is the complete declaration; `call_parameters` excludes implicit
receivers. `returns` contains source return constructor/reference candidates;
`delegates` contains public receiver-call evidence. Neither contains executed
return values.

The API path's **`id` overrides the definition's `id`** in the combined object.
Use `method_id` or `expected_output.callable_id` to join the definition.
`class_id`/`defined_in` describe its declaring class, while `resource_class` can be
a subclass. Unreached definitions, constructors/properties without callable
paths, and standalone functions are not emitted as additional JSONL records;
use their CSV/SQLite declaration tables.

## `summary.json`

Top-level keys/types are:

```text
schema_version: integer, package: string, version: string, source_root: string,
support_sources: SourceReference[], counts: object<string, integer>,
issues: ScanIssue[]
```

The primary `package`/`version` remain primary after support merging. Counts
include supplied support declarations, and `issues` is deduplicated.

| Count key | What is counted |
| --- | --- |
| `modules` | Module records, including native presence. |
| `functions` | Retained function definition variants. |
| `function_parameters` | Complete function declaration parameters. |
| `function_transport` | Function transport-evidence records. |
| `module_members` | Public module binding records. |
| `classes` | Class declaration records. |
| `class_fields` | Direct declared field records. |
| `output_records` | Analyzer-produced callable-output records. |
| `unknown_outputs` | Outputs with `unknown`, `unannotated`, or `unresolved` status. |
| `partial_outputs` | Outputs with `partial` status. |
| `method_definitions` | All retained method definition variants. |
| `methods_with_api_path` | Distinct definition IDs appearing in API paths. |
| `methods_without_api_path` | Retained definitions without a path. |
| `methods_with_output_analysis` | Retained method IDs with analyzer output records. |
| `constructor_protocol_definitions` | Retained double-underscore method definitions. |
| `api_methods` | Resolved method-path records, not unique definitions. |
| `parameters` | Complete method declaration parameters, including receivers. |
| `resource_graph` | Resolved graph-transition records. |
| `transport_evidence` | Class-method transport-evidence records. |
| `dynamic_members` | Public instance-assignment records. |
| `issues` | Deduplicated diagnostics. |

## SQLite tables and relationships

The database has 28 tables. Below, `T` means SQLite `TEXT`, `I` means `INTEGER`;
boolean meanings use `I`. `PK` identifies the primary key. Every listed field is
written by the exporter; structured `output_json`/metadata values remain text
containing JSON rather than a separate SQL JSON type. `position` is zero-based.

| Table | Exact fields and types | Primary key |
| --- | --- | --- |
| `metadata` | `key:T, value:T` | `key` |
| `modules` | `name:T, file:T, is_package:I, source_kind:T` | `name` |
| `module_imports` | `module_name:T, position:I, name:T, target:T, kind:T, expression:T` | `module_name, position` |
| `module_exports` | `module_name:T, position:I, name:T` | `module_name, position` |
| `functions` | `id:T, module:T, name:T, signature:T, return_annotation:T, docstring:T, file:T, line:I, end_line:I, is_async:I, is_overload:I` | `id` |
| `function_parameters` | `function_id:T, position:I, name:T, kind:T, annotation:T, default_expression:T, required:I, implicit:I` | `function_id, position` |
| `function_decorators` | `function_id:T, position:I, expression:T` | `function_id, position` |
| `function_transport` | `id:T, function_id:T, module:T, file:T, line:I, call:T, operation:T, route:T, route_kind:T` | `id` |
| `module_members` | `id:T, module:T, name:T, kind:T, expression:T, annotation:T, file:T, line:I` | `id` |
| `classes` | `id:T, module:T, name:T, qualname:T, docstring:T, file:T, line:I` | `id` |
| `class_bases` | `class_id:T, position:I, expression:T` | `class_id, position` |
| `class_fields` | `id:T, class_id:T, name:T, annotation:T, default_expression:T, requiredness:T, file:T, line:I, kind:T` | `id` |
| `class_decorators` | `class_id:T, position:I, expression:T` | `class_id, position` |
| `methods` | `id:T, class_id:T, name:T, signature:T, return_annotation:T, docstring:T, file:T, line:I, end_line:I, is_async:I, is_property:I, is_overload:I, binding:T` | `id` |
| `method_decorators` | `method_id:T, position:I, expression:T` | `method_id, position` |
| `method_returns` | `method_id:T, position:I, expression:T` | `method_id, position` |
| `method_delegates` | `method_id:T, position:I, expression:T` | `method_id, position` |
| `parameters` | `method_id:T, position:I, name:T, kind:T, annotation:T, default_expression:T, required:I, implicit:I` | `method_id, position` |
| `transport_evidence` | `id:T, method_id:T, class_id:T, module:T, file:T, line:I, call:T, operation:T, route:T, route_kind:T` | `id` |
| `dynamic_members` | `id:T, class_id:T, name:T, expression:T, annotation:T, file:T, line:I` | `id` |
| `dynamic_member_targets` | `member_id:T, position:I, expression:T` | `member_id, position` |
| `api_methods` | `id:T, root_class:T, resource_class:T, defined_in:T, client_path:T, method_name:T, method_id:T, method_kind:T, inherited:I` | `id` |
| `resource_graph` | `id:T, root_class:T, source_class:T, target_class:T, source_path:T, target_path:T, member:T, kind:T, file:T, line:I` | `id` |
| `issues` | `position:I, kind:T, message:T, file:T, line:I` | `position` |
| `method_outputs` | `method_id:T, annotation:T, status:T, output_json:T` | `method_id` |
| `function_outputs` | `function_id:T, annotation:T, status:T, output_json:T` | `function_id` |
| `method_output_models` | `method_id:T, position:I, model_id:T` | `method_id, position` |
| `function_output_models` | `function_id:T, position:I, model_id:T` | `function_id, position` |

Relationships are enforced through foreign keys during export:

- `module_name` in module child tables references `modules.name`. Declaration
  `module` columns in `classes`, `functions`, `module_members`, and transport
  tables reference `modules.name`.
- `methods.class_id`, class child tables, `dynamic_members.class_id`, and
  `transport_evidence.class_id` reference `classes.id`.
- Method child tables and `api_methods.method_id` reference `methods.id`;
  function child tables reference `functions.id`. `dynamic_member_targets.member_id`
  references `dynamic_members.id`.
- `api_methods.root_class`, `resource_class`, and `defined_in`, and the graph's
  `root_class`, `source_class`, and `target_class`, reference `classes.id`.
- Output tables have one row per corresponding retained definition. Model-link
  owner IDs reference `method_outputs`/`function_outputs`, and `model_id`
  references `classes.id`. Output field details stay nested in `output_json`;
  there is no separate normalized projected-output-fields table.

SQLite uses `default_expression` where CSV/JSON use `default`. Scalar declaration
tables do not duplicate CSV JSON collections; their ordered child tables store
those values. `metadata` keys are `schema_version`, `package`, `version`,
`source_root`, and `support_sources`; counts and diagnostics are in the summary
and `issues`, not additional metadata rows. Metadata values are text, including
schema version `"1"` and a JSON array for support sources.

For example, join definition and output data without requiring a graph path:

```sql
SELECT m.class_id, m.name, m.binding, o.status, o.annotation, o.output_json
FROM methods AS m
JOIN method_outputs AS o ON o.method_id = m.id
ORDER BY m.class_id, m.name, m.line;
```

## Worked examples

### Bundled offline demo

From the project directory after installation:

```bash
sdk-atlas --package demo_sdk --source examples --out ./inventory --print-summary
```

The current demo retains `Things.__init__`, `Things.retrieve`, and the
`Client.things` property in `methods.csv`. Only `client.things.retrieve` is a
callable API path. `normalize_identifier` is a standalone function. The ordinary
private `_get` helper is excluded from retained method definitions.

The retrieve caller argument is:

```json
{"name":"thing_id","kind":"POSITIONAL_OR_KEYWORD","annotation":"str","default":"","required":true,"implicit":false}
```

Its expected output is:

```json
{
  "callable_id": "demo_sdk.client.Things.retrieve:client.py:13",
  "annotation": "dict[str, str]",
  "model_ids": [],
  "fields": [],
  "status": "type_only",
  "unresolved": []
}
```

The dictionary's runtime keys are not fabricated into a response model. The
class-method transport evidence separately retains `/things/{thing_id}` with
formatted-route evidence and a best-effort `get` operation.

Read CSV and its structured cells separately:

```python
import csv
import json
from pathlib import Path

csv.field_size_limit(16 * 1024 * 1024)
with Path("inventory/api_methods.csv").open(encoding="utf-8", newline="") as stream:
    for row in csv.DictReader(stream):
        arguments = json.loads(row["parameters"])
        output = json.loads(row["expected_output"])
        print(
            row["client_path"], [item["name"] for item in arguments], output["status"]
        )
```

For the bundled demo this prints:

```text
client.things.retrieve ['thing_id'] type_only
```

### Declared model fields

This generic fixture is adapted from the return-field regression tests. Put the
following source in `future/__init__.py` under a local fixture directory:

```python
class Result:
    count: int


class Entry:
    def generate(self, prompt: str, *, limit: int = 10) -> Result: ...
```

```bash
sdk-atlas --package future --source /path/to/fixture --root future.Entry --out ./future_inventory
```

The method has caller arguments `prompt` (required) and `limit` (default text
`"10"`, optional); its complete declaration also includes implicit `self`.
The path is `client.generate`, and the expected-output object is:

```json
{
  "callable_id": "future.Entry.generate:__init__.py:6",
  "annotation": "Result",
  "model_ids": ["future.Result"],
  "fields": [
    {
      "model_id": "future.Result",
      "class_id": "future.Result",
      "name": "count",
      "annotation": "int",
      "default": "",
      "requiredness": "required",
      "inherited": false
    }
  ],
  "status": "resolved",
  "unresolved": []
}
```

No function call or object construction is needed to produce this evidence.

## Coverage and bounds

Python source and readable stubs determine recoverable declarations. Native
modules are recorded with `native_module` diagnostics, not imported to enumerate
compiled symbols. Static import/re-export names are evidence even when a full
native signature is unavailable. Runtime monkey-patching, metaclasses, generated
exports, serializers, and custom descriptors are not fully reconstructed.

Support dependencies are opt-in: no recursive import traversal, target imports,
or automatic support installation occurs. Imported bases outside supplied source
produce `external_base`; unbound/missing in-scope bases produce `unresolved_base`.
Recognized built-in bases do not produce those warnings, and their native
methods are not reconstructed.

Default bounds are 100,000 files per supplied source, 8 MiB per source file,
500,000 AST nodes per file, 32 graph/inheritance/alias levels, and a 100,000
`--max-paths` budget for separately bounded graph/inheritance/member/output work.
Annotation nesting also has a fixed bound, and distinct union projections have
a separate field-reference budget. See the README for configurable flags.

Exceeding the source file-count limit aborts before publication. File byte/AST
limits, unreadable/syntactically invalid files, native presence, and graph/output
truncation can instead produce diagnostics with a partial inventory. The
`source_changed` diagnostic reports a detected size/mtime change across an opened
file read; read allocation uses the opened file's bounded size. This detects
ordinary concurrent edits but does not create an atomic snapshot of an entire
changing source tree. Inheritance/field-limit markers can keep output status
partial even when some local model fields are known.

Inspect diagnostics and counts before treating an export as complete. Bounds
limit reconstruction work; they do not make arbitrary dynamic or native APIs
fully recoverable. See [coverage and performance review](review.md) and
[architecture](architecture.md) for the measured scope and implementation.
