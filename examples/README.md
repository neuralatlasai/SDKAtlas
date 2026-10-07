# Offline demonstration

The synthetic package under `demo_sdk/` has module functions, a constant, a
client, a property-backed resource, and a path-bearing method. It contains no
API credentials or external service.
Run from the project root after installing SDK Atlas:

```bash
sdk-atlas --package demo_sdk --source examples --out ./inventory --print-summary
```

Inspect `inventory/summary.json`, `inventory/api_methods.csv`, and
`inventory/transport_evidence.csv`, together with `inventory/modules.csv`,
`inventory/functions.csv`, and `inventory/module_members.csv`.
The source is input data for static analysis;
the scanner does not execute its methods. Resource and transport names are
deliberately generic and are not part of a scanner allow-list.

Pass the import name of another installed package explicitly. These examples
require the corresponding package's source or stubs to be present; SDK Atlas
itself does not install these third-party libraries as dependencies.

```bash
sdk-atlas --package numpy --out ./numpy_inventory
sdk-atlas --package vllm --out ./vllm_inventory
sdk-atlas --package transformers --out ./transformers_inventory
sdk-atlas --package anthropic --out ./anthropic_inventory
sdk-atlas --package agents --out ./agents_inventory
```

For a native extension, static wrappers and available `.pyi` files determine how
much of its API can be recovered. No target module is imported to enumerate
runtime-created symbols.
