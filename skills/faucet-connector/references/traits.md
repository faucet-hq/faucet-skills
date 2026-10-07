# The Source and Sink traits

Both traits live in `faucet-core` (`crates/core/src/traits.rs`) and are re-exported at the crate root: `use faucet_core::{Source, Sink, StreamPage, FaucetError, Value};`. Both are `#[async_trait]`, `Send + Sync`, and object-safe, so the pipeline and the CLI hold them as `Box<dyn Source>` / `Box<dyn Sink>`. Every method except one per trait has a default, so a new core release never breaks an existing connector.

Signatures below are copied from `faucet-core` 1.13.

## Source

Required (the only method without a default):

```rust
async fn fetch_with_context(
    &self,
    context: &std::collections::HashMap<String, Value>,
) -> Result<Vec<Value>, FaucetError>;
```

`context` holds the parent record's fields when the source runs as a child in a parent/child matrix. A root source receives an empty map. Use `faucet_core::util::substitute_context` to resolve `{placeholder}` tokens if your source supports being a child; otherwise ignore it.

Defaults you will usually override:

```rust
fn stream_pages<'a>(
    &'a self,
    context: &'a std::collections::HashMap<String, Value>,
    batch_size: usize,
) -> Pin<Box<dyn Stream<Item = Result<StreamPage, FaucetError>> + Send + 'a>>;
```

The default calls `fetch_with_context_incremental`, holds the whole result in memory, and chunks it. Override it whenever the backend has a paging primitive (cursor, keyset, scroll, offset, consumer). See `streaming-and-batching.md`.

```rust
fn config_schema(&self) -> Value;            // default: empty object schema
fn connector_name(&self) -> &'static str;    // default: Rust type name
fn dataset_uri(&self) -> String;             // default: "<connector_name>://unknown"
```

Override all three. `config_schema` powers `faucet schema`, `faucet init` and editor completion. `connector_name` is the `connector` metric label and must be a short, stable, non-empty snake_case string. `dataset_uri` is the lineage identity and must not contain credentials.

Resumable (incremental) sources override:

```rust
fn state_key(&self) -> Option<String>;                                       // default None
async fn apply_start_bookmark(&self, _bookmark: Value) -> Result<(), FaucetError>; // default no-op
async fn fetch_with_context_incremental(
    &self,
    context: &std::collections::HashMap<String, Value>,
) -> Result<(Vec<Value>, Option<Value>), FaucetError>;                       // default: (records, None)
```

`state_key()` returning `Some` opts the source into resume. The key must pass `faucet_core::state::validate_state_key` (ASCII letters, digits, `_ - : . /`, max 256 chars, no leading dot, no `..` segment). The CLI may replace it with its own `{pipeline}::{row}` scheme, so never rely on reading it back. `apply_start_bookmark` receives the last persisted bookmark before streaming starts; store it behind interior mutability (`std::sync::Mutex<Option<Value>>`) and read it at the start of `stream_pages`. If a stored bookmark has the wrong shape, return `FaucetError::State`, never silently restart from the beginning.

If your bookmark shape changes later, bump `fn state_schema(&self) -> u32` (default `0`) and teach `fn migrate_state(&self, from: u32, data: Value) -> Result<Value, FaucetError>` the step.

Optional capabilities. Return `true` only if the matching methods genuinely work; the CLI gates configs on these:

| Probe (default `false`) | Implement with | Meaning |
|---|---|---|
| `supports_exactly_once()` | per-page bookmarks, deterministic replay | Same bookmark always replays the same page sequence (CDC-style). Required for `delivery: exactly_once` via atomic watermark. |
| `supports_discover()` | `discover()` | `faucet discover` can list datasets. |
| `is_shardable()` | `enumerate_shards()`, `apply_shard()` | Work can be split for clustered runs. |
| `consumes_destructively()` | | Reading acks/deletes data (queues). Callers refuse previews and dry runs. |

`check(&self, ctx: &CheckContext) -> Result<CheckReport, FaucetError>` defaults to pulling one page with `stream_pages(&empty, 1)`. Override it only when the first page blocks (a listener) or has side effects (consuming a replication slot). A failed probe is `Ok(CheckReport::single(Probe::fail(..)))`, never `Err`.

Do not override `fetch_all` or `fetch_all_incremental`; they are conveniences that call the `_with_context` versions.

If you override `stream_pages`, make `fetch_with_context` / `fetch_with_context_incremental` drain your stream so every entry point returns the same data (see `examples/faucet-source-acme/src/stream.rs`).

## Sink

Required:

```rust
async fn write_batch(&self, records: &[Value]) -> Result<usize, FaucetError>;
```

Return the number of records written. An empty slice must be a cheap `Ok(0)`.

Defaults you will usually override:

```rust
async fn flush(&self) -> Result<(), FaucetError>;   // default: no-op
fn config_schema(&self) -> Value;
fn connector_name(&self) -> &'static str;
fn dataset_uri(&self) -> String;
async fn check(&self, _ctx: &CheckContext) -> Result<CheckReport, FaucetError>; // default: not_implemented
```

Override `flush` if `write_batch` buffers anything (a file writer, a multipart upload, a client-side batch). After `flush` returns `Ok`, everything written so far must be durable and visible. Override `check` with a read-only connect/auth/metadata probe; it must not insert anything and must not put credentials in a probe reason.

Per-row results for the dead-letter queue:

```rust
async fn write_batch_partial(&self, records: &[Value]) -> Result<Vec<RowOutcome>, FaucetError>;
fn batch_atomicity(&self) -> faucet_core::BatchAtomicity;   // default BestEffort
```

`RowOutcome` is `Result<(), FaucetError>`. Override `write_batch_partial` when the API reports per-row success, or when some rows are invalid before you send (missing key). Return one outcome per input row, in order. An outer `Err` means the whole call failed. Override `batch_atomicity` to `Atomic` only if a failed write commits nothing, or `PerRow` only if per-row outcomes are exact and an outer `Err` commits nothing. Leave it `BestEffort` otherwise.

Write modes (upsert/delete/overwrite):

```rust
fn supported_write_modes(&self) -> &'static [faucet_core::WriteMode]; // default &[WriteMode::Append]
fn dedups_by_key(&self) -> bool;                                      // default false
fn write_batch_is_replay_safe(&self) -> bool;                         // default self.dedups_by_key()
```

Flatten `faucet_core::WriteSpec` into the config, list only the modes you really apply, and return `self.config.write.dedups_by_key()` from `dedups_by_key`. The default `write_batch_is_replay_safe` then follows the live config. Route keyed writes through `faucet_core::plan_writes` (it dedups within a page and reports rows with a missing or null key as `failed`). Overwrite also needs `is_overwrite`, `begin_overwrite`, `commit_overwrite`, `abort_overwrite`; their defaults return typed "unsupported" errors so a half-implemented overwrite fails loudly.

Effectively-once by atomic watermark:

```rust
fn supports_idempotent_writes(&self) -> bool;   // default false
async fn write_batch_idempotent(
    &self,
    records: &[Value],
    scope: &str,
    token: &str,
) -> Result<usize, FaucetError>;                 // default: ignores token, calls write_batch
async fn last_committed_token(&self, scope: &str) -> Result<Option<String>, FaucetError>; // default None
```

See `reliability.md` before turning this on.

Schema drift: `current_schema()`, `supports_schema_evolution()`, `evolve_schema()`. Evolution must be idempotent additive DDL (`ADD COLUMN IF NOT EXISTS` semantics).

Other defaulted hooks (`supports_columnar`/`write_batch_columnar` behind the `arrow` feature, `native_load_capabilities`/`load_native`, `supports_cleanup`/`cleanup_scope`, `supports_rollback`/`rollback_run`, `complete_run`, `local_outputs`, `set_roundtrip_recorder`) are optional fast paths and run-lifecycle hooks. Leave them at their defaults until you need them; each default is either a no-op or a typed "unsupported" error.

## Object safety rules

- No generic methods, no associated types, no `Self`-returning methods, no `impl Trait` returns on your trait impls. Put generics on private helpers, not on the trait.
- Driver types (pools, SDK clients) live inside your struct, never in a trait signature.
- Keep a constructor that returns `Result<Self, FaucetError>` and does no network I/O, so a custom binary can register it with a synchronous factory (see `publishing.md`).
