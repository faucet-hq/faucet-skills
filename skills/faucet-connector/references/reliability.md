# Reliability: ordering, idempotency, retries, errors

Silent duplication or loss at the destination is the worst bug a connector can ship. These rules are what the pipeline relies on.

## Write, then flush, then checkpoint

For every page the pipeline does, in this order:

1. `sink.write_batch` / `write_batch_partial` / `write_batch_idempotent`
2. `sink.flush()`
3. `StateStore::put(state_key, bookmark)`

The bookmark never moves ahead of durable data. What that requires from you:

- **Source:** never advance or persist a position yourself. Emit it on a `StreamPage` and let the pipeline persist it after the flush. Do not commit consumer offsets, ack messages, or delete files before the page that contains them has been written and flushed; if your backend needs an ack, do it when the next page is polled (the pipeline only polls again after the previous page is durable) and set `consumes_destructively() -> true`.
- **Source:** emitted bookmarks must only move forward with respect to the records already emitted.
- **Sink:** when `flush()` returns `Ok`, everything passed to `write_batch` so far must be durable and readable. If `write_batch` writes through immediately, the default no-op `flush` is fine. If it buffers, `flush` must commit.
- The crash window between flush and checkpoint is deliberate: the page is re-read on the next run. Default delivery is therefore at-least-once, and every sink must tolerate seeing a page twice.

## Delivery guarantees ("effectively-once")

There are two ways to get no duplicates across retries and resumes:

| Mechanism | Sink must | Source must |
|---|---|---|
| Keyed upsert | list `WriteMode::Upsert` (and/or `Delete`) in `supported_write_modes()`, return `self.config.write.dedups_by_key()` from `dedups_by_key()`, and really converge on the key | nothing |
| Atomic watermark | return `true` from `supports_idempotent_writes()`, commit rows and token in one transaction in `write_batch_idempotent`, read the token back in `last_committed_token` | return `true` from `supports_exactly_once()` (deterministic replay) |

Call it "effectively-once" in docs, never "exactly-once" in the distributed-consensus sense.

Atomic watermark contract:

- `write_batch_idempotent(records, scope, token)` writes the records **and** stores `token` under `scope` in a single atomic unit (same database transaction, same object commit, same producer transaction). Two separate writes do not qualify.
- Store `token` verbatim and never parse it. It may carry a bookmark suffix that only the pipeline decodes.
- `last_committed_token(scope)` returns the stored token durably, or `None` if the scope has never committed.
- If you cannot do all of this, leave `supports_idempotent_writes()` at `false`. An honest `false` makes the pipeline refuse `delivery: exactly_once` at config load; a false `true` corrupts data.

## Retries

There are two layers.

**Inside your connector (source reads, metadata calls).** Retry transient failures yourself with the shared helper:

```rust
pub async fn execute_with_retry<F, Fut, T>(
    max_retries: u32,
    base_backoff: Duration,
    operation: F,
) -> Result<T, FaucetError>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<T, FaucetError>>;
```

It retries only errors where `FaucetError::is_retriable()` is true, with capped exponential backoff and jitter. Expose `max_retries` in config. Safe to use for reads and for idempotent calls.

**Around sink writes (the pipeline).** When a resilience policy is attached, the pipeline retries `flush` and state writes, but it retries a plain `write_batch` / `write_batch_partial` **only when `sink.write_batch_is_replay_safe()` is true**. That defaults to `dedups_by_key()`, so a keyed-upsert sink gets retries and an append sink does not. `write_batch_idempotent` is always retried because a replayed token-stamped write is a no-op.

- `supports_idempotent_writes()` does **not** make a plain `write_batch` retryable. A sink can support the token protocol and still have a `write_batch` that is a bare multi-row insert.
- Override `write_batch_is_replay_safe()` to `true` only if a plain `write_batch` converges when replayed by construction (for example, every write is a keyed `PUT`).
- Never retry a non-idempotent write inside your own `write_batch` either. If the server committed and the response was lost, a retry duplicates every row. Let the error propagate; the run fails and the page is replayed from the last checkpoint.

## Error typing

Every fallible path returns `Result<_, FaucetError>`. Pick the variant that names the failure (`crates/core/src/error.rs`):

| Situation | Variant |
|---|---|
| Bad or missing config, unsupported option, invalid state key | `Config(String)` |
| Transport failure from `reqwest` | `Http(reqwest::Error)` (via `?`) |
| Non-2xx HTTP response | `HttpStatus { status, url, body }` (truncate `body`) |
| 429 with a wait hint | `RateLimited(Duration)` |
| Malformed JSON | `Json(serde_json::Error)` (via `?`) |
| Credential acquisition or refresh failed | `Auth(String)` |
| A stored bookmark you cannot read | `State(String)` |
| Other runtime failure in a source / sink | `Source(String)` / `Sink(String)` |
| A driver or library error type you want to keep | `Custom(Box<dyn std::error::Error + Send + Sync>)` |

Notes:

- `is_retriable()` is true for `Http` without a status, `HttpStatus` with 5xx or 429, and `RateLimited`. Everything else fails fast. So type transient failures as one of those, and permanent ones as anything else. Do not wrap a 503 in `Sink(String)`; it will not be retried.
- `FaucetError::sink_status(Some(status), url, message)` builds `HttpStatus` for 429/5xx and `Sink` for anything else. Use it in sinks.
- Third-party error types go through `Custom` so the error chain survives: `FaucetError::Custom(Box::new(driver_err))`, or `?` on a `Box<dyn Error + Send + Sync>`. Stringifying into `Source`/`Sink` loses the chain.
- `FaucetError` is `#[non_exhaustive]`; match it with a `_` arm.
- No `.unwrap()` / `.expect()` on anything that can fail at runtime (network, parsing, config, I/O, lock poisoning). `.expect()` is allowed only for an invariant established in the constructor, with a message naming it.
- Never put a token, password or connection string in an error message or log line. Error bodies from the server can echo request data; truncate them.

## Failure modes to handle explicitly

- Empty page in the middle of a stream.
- A record missing the cursor or key field (error, never skip).
- A malformed or unknown-shape bookmark (`State` error, never restart from zero silently).
- A request that committed server-side but whose response was lost (do not retry a non-idempotent write).
- Partial batch failure: per-row errors through `write_batch_partial`, otherwise fail the whole call.
- Cancellation: the pipeline stops at a page boundary and calls `flush`. Make `flush` safe to call at any time.
