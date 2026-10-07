# Performance

A connector should be the fastest correct way to move data between its endpoints in Rust. These are review criteria, not optional tuning. None of them may trade away the ordering and idempotency rules in `reliability.md`.

## Build clients once

Create HTTP clients, database pools, object-store clients and producers in `new()` and keep them on the struct. Never build one inside `write_batch`, `stream_pages`, or a per-record loop.

```rust
// Good: built once, pooled, reused for every request of every run.
pub fn new(config: AcmeSourceConfig) -> Result<Self, FaucetError> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(config.timeout_secs))
        .build()?;
    Ok(Self { config, client, /* ... */ })
}

// Bad: a new client, TLS handshake and connection pool per batch.
async fn write_batch(&self, rows: &[Value]) -> Result<usize, FaucetError> {
    let client = reqwest::Client::new();
    /* ... */
}
```

The constructor should not do network I/O. A custom-binary factory is synchronous, and `faucet validate` builds connectors without contacting anything. Use lazy connections (`connect_lazy` for `sqlx` pools, connect on first use for others) and keep connection checks in `check()`.

## Pool with a bound

Database connectors expose `max_connections` (built-in defaults: 10 for sources, 5 for sinks). Never open unbounded connections.

## Use bulk APIs

- SQL sinks: one multi-row `INSERT ... VALUES (...), (...)` or `COPY` per batch, wrapped in a transaction where the store supports it. One statement per record is a defect.
- HTTP sinks: the backend's bulk or batch endpoint, one request per chunk.
- Key-value stores: pipelining or multi-key commands.
- Split a page into chunks only to respect the backend's request limit, and make that limit a config field.

## Bound concurrency

Parallel I/O (many objects, many partitions, fan-out HTTP) uses a bounded primitive: `buffer_unordered(concurrency)` or a semaphore. Expose `concurrency` with a sane default. Concurrency must never change the order in which pages are emitted, because bookmarks are persisted in emit order.

## Stream, do not buffer

Override `stream_pages` (see `streaming-and-batching.md`). Memory must stay O(page size). Do not collect the whole result set when the backend can page.

## Buffered and blocking I/O

- Wrap file and socket writers in a buffered writer and commit in `flush()`.
- CPU-heavy or blocking work (synchronous drivers, CSV/Parquet encoding, compression) goes on `tokio::task::spawn_blocking` so the async runtime is never stalled.
- Parse responses from bytes (`serde_json::from_slice`) rather than going through an intermediate `String`.

## Keep the hot path lean

- Do not log per record. Use `tracing::debug!` per page at most, with structured fields (`records = n`), and never log secrets.
- The pipeline already records metrics and spans for every source and sink call. Do not add your own per-record metrics; never use record IDs, URLs or cursors as metric labels.
- Avoid cloning records you are about to serialize; pass `&[Value]` straight to the serializer.

## Measure before optimizing

A change made for speed needs a benchmark or profile showing the hot path. Reducing round trips (bigger requests, fewer flushes, pipelining) almost always beats micro-optimizing allocations.
