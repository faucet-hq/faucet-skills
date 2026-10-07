# Naming, versioning and publishing

## Names

- Crate: `faucet-source-<name>` or `faucet-sink-<name>`, where `<name>` is the lowercase system name (`acme`, `acme-widgets`). It is also the YAML `type:` value.
- Shared code for a source/sink pair: `faucet-common-<name>`.
- `connector_name()` returns the same short name (`"acme"`).
- crates.io keywords start with the system name so the crate ranks for it: `keywords = ["acme", "etl", "pipeline", "connector", "data"]`.

## Versions

- Start at `version = "1.0.0"`, never `0.x`. `faucet new connector` already does this.
- Depend on `faucet-core = "1"` (major only).
- Adding an optional config field, a new enum variant, or a defaulted method is a **minor** release. Construct config types through serde, `new()`, `Default` or builder methods, and document that struct-literal construction and exhaustive matching are not part of your stable API. Mark growing public enums `#[non_exhaustive]`.
- If you run `cargo-semver-checks`, encode that contract so it agrees:

  ```toml
  [package.metadata.cargo-semver-checks.lints]
  constructible_struct_adds_field = "allow"
  constructible_struct_adds_private_field = "allow"
  enum_struct_variant_field_added = "allow"
  ```

- Removing or renaming a field, changing a default's meaning, or changing a bookmark shape without `state_schema()` / `migrate_state()` is a breaking change: either keep compatibility (accept the old name with `#[serde(alias = "...")]`, migrate old bookmarks) or release a new major.

## docs.rs

The scaffold sets both pieces; keep them:

```toml
[package.metadata.docs.rs]
all-features = true
rustdoc-args = ["--cfg", "docsrs"]
```

and `#![cfg_attr(docsrs, feature(doc_cfg))]` as the first line of `lib.rs`.

## Pre-publish checklist

- `cargo test`, `cargo clippy --all-targets -- -D warnings`, `cargo fmt --check` all pass.
- `tests/conformance.rs` runs every applicable check from `testing-and-conformance.md`.
- README shows a working `source:` or `sink:` YAML block with `${env:...}` for secrets, every config field with its default, the supported write modes and delivery guarantee (say "effectively-once", not "exactly-once").
- `license`, `repository`, `description`, `readme` set in `Cargo.toml`.
- `cargo publish --dry-run` succeeds, then `cargo publish`.

## Making it usable from faucet.yaml

The stock `faucet` binary only contains built-in connectors. Users run a third-party connector by building their own binary that registers it with `PluginRegistry`:

```toml
[dependencies]
faucet-cli = "1"
faucet-core = "1"
faucet-source-acme = "1"
```

```rust
use faucet_cli::registry::PluginRegistry;
use faucet_core::{schema_for, serde_json};
use faucet_source_acme::{AcmeSource, AcmeSourceConfig};

fn main() -> std::process::ExitCode {
    let registry = PluginRegistry::with_builtins().register_source_with(
        "acme",
        |cfg| Ok(Box::new(AcmeSource::from_value(cfg)?)),
        || serde_json::to_value(schema_for!(AcmeSourceConfig)).unwrap_or_default(),
        "acme source",
    );
    faucet_cli::run_main(registry)
}
```

The factory is synchronous (`Fn(Value) -> CliResult<Box<dyn Source>>`), which is why constructors must not do network I/O. Give your connector a `from_value(Value) -> Result<Self, FaucetError>` constructor that maps a deserialization failure to `FaucetError::Config`; a `FaucetError` converts into the CLI error with `?`, a raw `serde_json::Error` does not. `faucet install` prints a recipe that assumes this constructor. `register_sink_with` is the sink equivalent. A name that collides with a built-in is rejected at startup. With the binary built, `source: { type: acme }` works in `run`, `validate`, `schema`, `list`, `preview` and `serve`. Custom connectors receive their `config` block verbatim; the shared top-level `auth:` catalog is not injected.

## Listing it in the registry index

`faucet search`, `faucet list --available` and `faucet install` read a registry index embedded in the CLI (`cli/connectors/registry.json` in the faucet-stream repository). To be discoverable, open a pull request there adding an entry:

```json
{
  "name": "acme",
  "kind": "source",
  "verified": false,
  "crate": "faucet-source-acme",
  "description": "acme API source with keyset pagination and resume",
  "keywords": ["acme"],
  "core_compat": "1",
  "tier": "experimental"
}
```

Community entries set `verified: false` and an explicit `crate`. `tier` is optional for community connectors. Then users find it with:

```bash
faucet search acme
faucet install acme --kind source
```

`faucet install` only prints the recipe (for a community connector, the custom-binary snippet above); it never downloads or runs code. You can also test an unpublished entry with a local index: `faucet search acme --index ./registry.json`.
