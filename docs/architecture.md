# Source-derived inventory

SDK Atlas separates source discovery, AST collection, support-source merging,
symbol/class resolution, expected-output projection, and export. The required
import-package argument selects any available Python module or package. There
is no vendor default or maintained product catalog. Target and support code are
never imported or executed, and no API credentials are required.

## Source evidence and explicit dependencies

Discovery reads Python source and available `.pyi` stubs, and records opaque
native-extension module presence. AST collection retains module functions,
parameters, imports, assignment aliases, constants, classes, inheritance,
decorators, public class fields, dynamic instance assignments, and path-bearing
calls. Defaults and annotations are stored as text rather than evaluated.

Only the primary target is scanned by default. `--dependency IMPORT_NAME` adds
installed filesystem source, and `--dependency-source IMPORT_NAME=PATH` adds a
local repository, package directory, module, or stub. Both flags are repeatable.
No transitive dependency discovery or automatic dependency installation occurs.
`--latest` applies only to the target and installs a compatible wheel into a
temporary location with dependencies and source builds disabled.

Merging preserves the primary package/root/version and adds `support_sources`
provenance for each dependency. Declaration locations stay relative to their
original package roots. Module/class identity identifies the defining namespace;
the provenance mapping provides its source root. All declared source tables can
include support records, while graph-root selection remains target-oriented.

The merger validates package prefixes through a trie and checks each record
family's identities in one pass. Duplicate namespaces, overlapping parent/child
namespaces, or repeated module/class/callable/member/field IDs fail before export.
Inputs are immutable and remain unchanged after success or failure. String-key
indexes use average constant-time lookup; package checks do not compare every
dependency against every other dependency.

## Symbols, inheritance, and public paths

Import bindings retain whether they came from an actual import or an assignment
alias. Alias resolution preserves the defining module's scope and caches results
by module/reference. An unbound assignment such as `Alias = Ghost.Model` remains
unresolved rather than inventing a known external import.

An imported base outside the supplied source namespaces produces `external_base`
and suggests including the dependency. An unbound or missing in-scope base
produces `unresolved_base`; cycles and depth limits retain their diagnostics.
Recognized Python built-in bases produce neither warning. Their native members
are not reconstructed, and local runtime bindings can shadow built-in names.

Resolved classes use bounded C3 ancestry and normal child-member override rules.
Default graph roots prefer public package class exports, then structural roots
and public classes in the primary target. Support-only base classes are excluded
from automatic fallback roots. A support class can be selected through an
explicit target `__all__` export or `--root`. Each root uses the display prefix
`client`, with `root_class` retaining its qualified identity.

Descriptor and instance-assignment transitions project reachable methods onto
public paths. Method `binding` records recognized instance/class/static behavior;
the first declared positional receiver of instance/class methods is marked `implicit`.
Complete declaration parameters are retained, while caller-facing parameter
lists exclude that receiver. Custom decorators can change runtime behavior beyond
the conventions recognized statically.

A public method can be a transport method, delegating helper, or SDK helper.
Transport names and path expressions are evidence, not proof of a remote HTTP
contract. No record certifies that a server endpoint exists or that credentials
can invoke it.

## Expected return structures

Return analysis covers every discovered method and supplied module function,
independently of graph roots or path depth. This includes constructor/protocol
definitions, properties, and class-local callable aliases. It resolves annotation
references, including classes inside
recognized container/union forms, without evaluating them. Unannotated methods
can also supply a class constructor/reference as weaker inferred evidence.
`Literal` values and `Annotated` metadata are not treated as response models;
`Callable` argument/return metadata does not describe fields of the callable's
immediate result.

`expected_output` retains the full annotation, resolved `model_ids`, projected
fields, status, and unresolved references. Status is `resolved`, `inferred`,
`type_only`, `partial`, `unresolved`, `unannotated`, or `unknown`; the README defines
each state. Older manually constructed inventories without analysis records
receive an explicit unknown fallback when exported. A resolved annotation is still source evidence,
not validation of an actual returned object or wire payload.

Field projection follows resolved ancestry, child overrides, and recognized
`TypedDict` requiredness declarations. Fields retain their model, declaring
class, annotation, default text, `requiredness`, and inherited flag. `ClassVar`
fields remain in declared evidence but are excluded from instance output fields.
Properties and dynamic public instance assignments can contribute fields with
unknown requiredness. Nested field types are retained as annotation text rather
than recursively materialized into a complete schema. Validators, aliases,
serializers, factory defaults, and arbitrary model frameworks are not executed.

## Bounds, reuse, and export

Preprocessing indexes modules, symbols, classes, methods, fields, and members.
C3 merging uses indexed tail counts and a priority queue of eligible heads;
its merge cost is O(M log B), where M is expanded sequence entries and B is the
number of parent sequences. Alias, transition, effective-method, model-field,
and return-annotation results are cached rather than recomputed for every path.
Assignment alias expressions retain their defining module and expand through an
import prefix trie without evaluation. Single-model projections reuse one field
tuple; distinct union projections are cached under a separate reference budget.

Source file count and per-file byte limits apply separately to every explicitly
selected source. Each parsed AST has a node limit. Graph state count, inheritance
expansion, effective-member work, and response-field expansion have bounded
budgets derived from `--max-paths`; these are separate budgets rather than one
combined global counter. Graph/inheritance/alias depth uses `--max-depth`, and
return-annotation traversal also has a fixed nesting bound. Limit diagnostics
mark partial evidence instead of silently claiming complete coverage.

Export writes 14 artifacts. CSV rows and JSON Lines are streamed, and SQLite
insertion consumes row iterators. A bounded 1,024-definition serialization cache
reuses method metadata, caller parameters, and expected outputs across repeated
public paths. Serialization cost still scales with the requested output size.
`methods.csv` preserves all discovered method variants, complete declarations,
caller arguments, docstrings, outputs, and associated client paths. Empty path
associations remain explicit. Summary counts separate declaration coverage from
graph reachability; neither is a claim about unavailable native/runtime APIs.

SQLite keeps declaration fields in `class_fields`, complete method/function
parameters in their existing tables, expected structures in `method_outputs`
and `function_outputs`, and normalized model links in `method_output_models`
and `function_output_models`. Output field details remain nested in `output_json`.
Summary and database metadata retain primary and support-source provenance.

Artifacts are staged before publication. Replacing owned files in an existing
directory supports rollback on failure and preserves unrelated files, but readers
are not isolated across the whole multi-file replacement. Applications requiring
a consistent snapshot should publish into a fresh directory and switch their
own pointer after completion.

## Static limits and runtime dependencies

AST analysis cannot fully reconstruct runtime monkey-patching, metaclass code,
dynamic exports, generated implementations, conditional execution, custom import
hooks, or arbitrary descriptors. Native symbols need readable Python wrappers or
stubs; identifying a `.so` or `.pyd` does not recover its compiled API. Built-in,
frozen, and zip-only modules without readable filesystem evidence need explicit
source/stubs. A newer syntax than the running parser can produce source errors.

Python standard-library modules provide parsing, metadata, serialization,
SQLite, temporary directories, and subprocess control. There are no runtime
third-party dependencies. `pip` is invoked only for target `--latest` mode;
development and build tools are declared separately in the `dev` extra.
