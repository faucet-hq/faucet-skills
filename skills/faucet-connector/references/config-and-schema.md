# Config structs and JSON Schema

## Dependencies

`faucet-core` is the only faucet crate a connector depends on. It re-exports the shared types (`crates/core/src/lib.rs`):

```rust
pub use async_stream;
pub use async_trait::async_trait;
pub use futures_core::{self, Stream};
pub use schemars::{self, JsonSchema, schema_for};
pub use serde_json::{self, Value, json};
pub use tokio_util::sync::CancellationToken;
```

So `use faucet_core::{async_trait, Value, json, JsonSchema, schema_for, Stream, async_stream};` covers the trait plumbing. Do not add `async-trait`, `serde_json`, `futures-core` or `async-stream` to your own manifest. The scaffold adds two direct dependencies only because derive macros need their crate in scope: `serde` (with `derive`) and `schemars`. Everything else in `[dependencies]` should be your backend's client library.

Use `faucet-core = "1"` (major only). Never pin a minor floor such as `"1.13"`; it forces a release of your crate whenever core releases.

## Layout

```
faucet-source-acme/
  src/lib.rs      #![cfg_attr(docsrs, feature(doc_cfg))] first, then re-exports
  src/config.rs   config struct + sub-enums, validation, no I/O
  src/stream.rs   (source) or src/sink.rs (sink): the only module that does I/O
  tests/          integration + conformance tests
```

## The config struct

```rust
use faucet_core::{FaucetError, JsonSchema};
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct AcmeSourceConfig {
    /// Base URL of the acme API.
    pub base_url: String,
    /// API token. Supply it as `${env:ACME_TOKEN}`.
    pub token: String,
    /// Records per request; overrides the pipeline batch-size hint.
    #[serde(default)]
    pub page_size: Option<usize>,
    #[serde(default = "default_timeout_secs")]
    pub timeout_secs: u64,
}
```

Rules:

- Derive `Serialize + Deserialize + JsonSchema` on the struct and every nested type and enum.
- Doc comments on fields become schema `description`s, which `faucet schema` and editors show. Write them for users.
- Every optional field has `#[serde(default)]` or `#[serde(default = "fn")]`. Defaults must be safe and fast (bounded concurrency, finite timeouts).
- `#[serde(deny_unknown_fields)]` catches typos at `faucet validate`. Serde does not support it together with `#[serde(flatten)]`, so drop it on a sink config that flattens `WriteSpec`.
- Custom-serde fields (a `Duration`, a `reqwest::Method`, a regex) need `#[schemars(with = "String")]` (or the matching type) so the schema stays valid.
- Enums use `#[serde(rename_all = "snake_case")]`.
- Auth uses the adjacently tagged shape every built-in uses: `auth: { type: bearer, config: { token: ... } }`, i.e. `#[serde(tag = "type", content = "config", rename_all = "snake_case")]`.
- Non-serializable runtime state (clients, compiled regexes) belongs on the connector struct, not the config. If it must sit on the config, mark it `#[serde(skip)]`.

## Secrets

- Never hardcode a credential, token, host or URL. Every endpoint and secret comes from config.
- Users pass secrets as `${env:VAR}`, `${file:PATH}` or a secrets-manager reference; the CLI resolves them before your constructor runs. Your connector just receives a string.
- Do not derive `Debug` on a struct that holds a secret. Implement `Debug` by hand and print `"<redacted>"` (see `examples/faucet-source-acme/src/config.rs`). Never log the config, a connection string, or an auth header.
- `dataset_uri()` must be credential-free. For a URL with userinfo use `faucet_core::redact_uri_credentials`, or build the URI from host and dataset name only.

## Validation

Validate in the constructor and return `FaucetError::Config` for anything wrong: empty names, zero sizes, unparseable URLs, unsupported write modes, an invalid state key. This makes `faucet validate` fail before any data moves instead of failing an hour into a run.

```rust
pub fn validate(&self) -> Result<(), FaucetError> {
    if self.collection.trim().is_empty() {
        return Err(FaucetError::Config("acme: `collection` must not be empty".into()));
    }
    Ok(())
}
```

Compile regexes, parse URLs and check enum combinations here, once.

## Exposing the schema

```rust
fn config_schema(&self) -> Value {
    faucet_core::serde_json::to_value(faucet_core::schema_for!(AcmeSourceConfig))
        .unwrap_or(Value::Null)
}
```

`faucet_conformance::assert_config_schema_valid(&source)` (sinks: `assert_config_schema_valid_value(&sink.config_schema(), sink.connector_name())`) checks the result is an object, recognized as a JSON Schema, and round-trips through `serde_json`.

## Write-mode config for sinks

Sinks that support upsert or delete flatten the shared spec so `write_mode`, `key` and `delete_marker` appear at the top level of `sink.config`, like every built-in:

```rust
#[serde(flatten)]
pub write: faucet_core::WriteSpec,
```

In the constructor call `self.write.validate()?` (rejects upsert/delete without a key) and reject any mode you did not list in `supported_write_modes()`. If you list `Upsert` but cannot apply a `delete_marker`, reject `delete_marker` with a `Config` error instead of ignoring it.

## Shared config for a source/sink pair

If you ship both a source and a sink, put the shared config (auth, connection, formats) in a `faucet-common-<name>` crate that both depend on and re-export. `faucet new connector <name> --kind source --common` scaffolds it.
