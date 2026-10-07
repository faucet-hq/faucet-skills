---
name: faucet-connector
description: >-
  Use when writing a new faucet source or sink connector in Rust: scaffolding a
  faucet-source-* or faucet-sink-* crate, implementing the Source or Sink trait
  from faucet-core, adding a config struct with a JsonSchema, streaming pages
  with bounded memory, making a sink idempotent or effectively-once (keyed
  upsert or atomic watermark), typing errors as FaucetError, passing the
  faucet-conformance battery, or publishing and registering a third-party
  connector crate.
license: Apache-2.0
---

# Building a faucet connector

A faucet connector is a Rust crate that implements one trait from `faucet-core`: `Source` (reads records) or `Sink` (writes records). Records are `serde_json::Value`. The pipeline owns paging, checkpointing, retries, metrics and the dead-letter queue; the connector owns talking to the backend correctly and fast.

Working, tested examples: `examples/faucet-source-acme/` (HTTP API source with keyset pagination and resume) and `examples/faucet-sink-acme/` (bulk HTTP sink with append and keyed upsert). Both build against `faucet-core` 1.x from crates.io and pass the conformance battery against a `wiremock` backend. Copy from them.

## Hard rules

1. **Depend only on `faucet-core`** among faucet crates (`faucet-core = "1"`, major only). It re-exports `async_trait`, `serde_json` (`Value`, `json!`), `schemars` (`JsonSchema`, `schema_for!`), `async_stream`, `Stream`. Add `serde` and `schemars` only for the derive macros, plus your backend's client library.
2. **Build clients once.** Create HTTP clients, pools and producers in `new()` and store them. Never build one per call, per page or per record. `new()` must not do network I/O.
3. **Never advance a position before a durable flush.** Sources emit bookmarks on `StreamPage`s and let the pipeline persist them after `write → flush`. Never ack, commit offsets or delete source data before that. A sink's `flush()` must leave everything written so far durable.
4. **Never retry a non-idempotent write.** The pipeline retries a plain `write_batch` only when `write_batch_is_replay_safe()` is true (default: `dedups_by_key()`). Do not retry writes inside `write_batch` yourself. `supports_idempotent_writes()` covers only the token path (`write_batch_idempotent`), not plain writes.
5. **Every failure is a typed `FaucetError`.** No `.unwrap()`/`.expect()` on runtime-fallible values. Transient failures map to `Http`, `HttpStatus` (5xx/429) or `RateLimited` so they are retried; permanent ones to `Config`, `Auth`, `Json`, `Source`/`Sink`, `State`. Wrap third-party error types in `FaucetError::Custom(Box<dyn Error + Send + Sync>)` instead of stringifying.
6. **No hardcoded credentials, hosts or URLs.** Everything comes from config; secrets arrive as `${env:VAR}` / secrets-manager references the CLI resolves. Don't derive `Debug` on secret-bearing config; don't log configs, auth headers or connection strings.
7. **Keep the traits object-safe.** Implement only the trait's own methods with their exact signatures; no generic trait methods, associated types or driver types in signatures. `Box<dyn Source>` / `Box<dyn Sink>` must work.
8. **Report capabilities honestly.** Return `true` from `supports_*`, `dedups_by_key`, `supported_write_modes` only for what the connector really does. The CLI gates configs on these; a false `true` corrupts data.

## Workflow

### 1. Scaffold

```bash
faucet new connector acme --kind source
faucet new connector acme --kind sink
faucet new connector acme --kind source --common --output ./crates
```

`<name>` is lowercase (`acme`, `acme-widgets`); it becomes the crate name `faucet-<kind>-<name>` and the YAML `type:`. `--common` also creates `faucet-common-<name>` for config shared by a source/sink pair. `--force` overwrites. The crate starts at version `1.0.0` with `src/lib.rs`, `src/config.rs`, `src/stream.rs` (source) or `src/sink.rs` (sink), docs.rs metadata and a passing unit test. Run `cargo test` immediately; it is green with a passthrough implementation.

### 2. Config and schema

In `src/config.rs`: one struct deriving `Serialize, Deserialize, JsonSchema`, doc comments on every field (they become the schema descriptions), `#[serde(default)]` on optional fields, `#[serde(deny_unknown_fields)]` unless you flatten `WriteSpec`, and a `validate()` that returns `FaucetError::Config`. Expose it with:

```rust
fn config_schema(&self) -> Value {
    faucet_core::serde_json::to_value(faucet_core::schema_for!(AcmeSourceConfig))
        .unwrap_or(Value::Null)
}
```

Details: `references/config-and-schema.md`.

### 3. Implement the trait

The only required methods:

```rust
// Source
async fn fetch_with_context(
    &self,
    context: &std::collections::HashMap<String, Value>,
) -> Result<Vec<Value>, FaucetError>;

// Sink
async fn write_batch(&self, records: &[Value]) -> Result<usize, FaucetError>;
```

Always also override `config_schema`, `connector_name` (short, stable, non-empty, e.g. `"acme"`) and `dataset_uri` (credential-free).

Source, in order of importance:

- Override `stream_pages` to read page by page from the backend's paging primitive. The default buffers the whole result. Honor `batch_size == 0` as "one page". Have `fetch_with_context` drain your stream.
- For incremental sync: `state_key()` returns `Some(valid key)`, pages carry `bookmark: Some(..)` (final page, or every page if replay from a bookmark is deterministic), and `apply_start_bookmark` stores the start position. A malformed bookmark is a `FaucetError::State`.
- `check()` only if the default one-page probe blocks or has side effects.

Sink, in order of importance:

- `write_batch` uses the backend's bulk API, splits to the request limit, returns `Ok(0)` for an empty slice.
- `flush` if you buffer anything.
- Upsert/delete: flatten `faucet_core::WriteSpec` into the config, list modes in `supported_write_modes()`, return `self.config.write.dedups_by_key()` from `dedups_by_key()`, route rows through `faucet_core::plan_writes`, and report missing-key rows through `write_batch_partial` instead of dropping them.
- Atomic watermark (`supports_idempotent_writes`, `write_batch_idempotent`, `last_committed_token`) only if rows and token commit in one transaction.
- `check()` with a read-only connect/auth probe.

Signatures and every defaulted method: `references/traits.md`. Paging and sizing: `references/streaming-and-batching.md`. Ordering, retries, errors, effectively-once: `references/reliability.md`. Client reuse, bulk APIs, concurrency: `references/performance.md`.

### 4. Unit and integration tests

- Unit tests in `#[cfg(test)]` at the bottom of each file: config validation, URL building, cursor/bookmark handling, error mapping, capability probes per config, `Debug` hides secrets.
- Integration tests in `tests/`: `wiremock` for HTTP backends (model pagination or a keyed store with a `Respond` impl), `testcontainers` for databases and queues. Cover multi-page reads, resume, a retried 5xx, a non-retried 4xx, a malformed response, per-row sink failures.
- Assert exact outcomes: record counts, bookmark values, error variants, `is_retriable()`.

### 5. Conformance

Add `faucet-conformance = "1"` as a dev-dependency and a `tests/conformance.rs` that runs every applicable check against the real connector:

```rust
conf::assert_config_schema_valid(&source);
conf::assert_bounded_memory(&source, 50, 230).await;
conf::assert_bookmark_roundtrip(&source).await;
conf::assert_capabilities_truthful(&sink, || std::future::ready(store.len())).await;
```

Give each sink check a fresh destination, configure keyed checks with `write_mode: upsert`, `key: ["id"]`, and assert the honest branch for capabilities you do not have. The full check list: `references/testing-and-conformance.md`.

The CLI command scores connectors compiled into the binary (built-ins); it cannot see a third-party crate:

```bash
faucet conformance --kind sink
faucet conformance --all --min-tier stable
```

Finish with:

```bash
cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

### 6. Publish

- Name `faucet-source-<name>` / `faucet-sink-<name>`, version `1.0.0`, keywords led by the system name, README with a YAML example using `${env:...}` for secrets.
- Additive changes (optional config field, defaulted method, new `#[non_exhaustive]` enum variant) are minor releases. Renames, removals and bookmark-shape changes without `migrate_state` are breaking.
- `cargo publish --dry-run`, then `cargo publish`.
- Users run it through a custom binary: `PluginRegistry::with_builtins().register_source_with("acme", |cfg| Ok(Box::new(AcmeSource::from_value(cfg)?)), schema_fn, "description")` passed to `faucet_cli::run_main`.
- To make it discoverable, open a PR adding a `verified: false` entry with an explicit `crate` to `cli/connectors/registry.json` in faucet-stream. Then:

```bash
faucet search acme
faucet install acme --kind source
faucet list --available
```

Details: `references/publishing.md`.

## Review checklist

- [ ] Only `faucet-core` (+ `serde`, `schemars`, backend client) in `[dependencies]`; `faucet-core = "1"`.
- [ ] Client/pool built once in `new()`; no I/O in `new()`; no I/O in `config.rs`.
- [ ] `stream_pages` streams natively; page size bounded; `batch_size == 0` handled.
- [ ] Bookmarks only on `StreamPage`s; never acked or committed early; malformed bookmark is a `State` error.
- [ ] Sink `flush` makes everything durable; empty batch is `Ok(0)`.
- [ ] No retry of non-idempotent writes; capability probes match behavior for every config.
- [ ] Every error typed; transient vs permanent mapped correctly; `Custom` for foreign errors; no `unwrap` on runtime values.
- [ ] No secrets in `Debug`, logs, errors, `dataset_uri`, or probe reasons.
- [ ] Unit + integration tests + `tests/conformance.rs` green; clippy and fmt clean.
- [ ] Version `1.0.0`, docs.rs metadata, README with YAML example.

## Reference files

- `references/traits.md` — exact `Source`/`Sink` signatures and which defaults to override
- `references/config-and-schema.md` — config structs, serde/schemars, re-exports, secrets, validation
- `references/streaming-and-batching.md` — `stream_pages`, bookmarks, batch sizing, buffering sinks
- `references/reliability.md` — write→flush→checkpoint, effectively-once, retry rules, `FaucetError` mapping
- `references/performance.md` — client reuse, pooling, bulk APIs, bounded concurrency
- `references/testing-and-conformance.md` — unit/integration tests, the conformance battery, `faucet conformance`
- `references/publishing.md` — naming, versioning, custom binaries, registry index
