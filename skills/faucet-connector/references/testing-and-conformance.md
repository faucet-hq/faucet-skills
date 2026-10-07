# Testing and conformance

Every non-trivial behavior gets a test, and each test asserts the specific outcome (the records, the bookmark, the exact error variant), not just "no panic".

## Unit tests

Put them in a `#[cfg(test)] mod tests` at the bottom of each source file. Cover the pure logic without network I/O:

- config validation: every `FaucetError::Config` branch, `deny_unknown_fields`, defaults
- URL / query / request building
- cursor and bookmark extraction, including a missing or null cursor field
- `apply_start_bookmark` with a malformed bookmark
- error mapping (status code to variant, `is_retriable()`)
- `Debug` output does not contain secrets
- capability probes for each config (`dedups_by_key`, `write_batch_is_replay_safe`, `supports_idempotent_writes`)

If a line is hard to test, extract the pure part into a function and keep the I/O shim thin.

## Integration tests

Put them in `tests/`. Use a real or faithful backend:

- HTTP APIs: `wiremock`. Implement `wiremock::Respond` to model pagination or a keyed store instead of hard-coding every response (see `examples/faucet-source-acme/tests/conformance.rs` and `examples/faucet-sink-acme/tests/conformance.rs`).
- Databases, queues, object stores: `testcontainers`, or a local file/tempdir for embedded stores.

Cover at least: a multi-page read, resume from a bookmark, a transient 5xx that is retried, a 4xx that is not, a 429 with `Retry-After`, a malformed response, and for sinks, per-row failures and the write modes you advertise.

## The conformance battery

`faucet-conformance` (crates.io, `faucet-conformance = "1"` as a dev-dependency) is the executable form of the connector contract. Passing it in CI is what makes a connector conformant. Call the checks that apply from `tests/conformance.rs`:

| Check | Applies to |
|---|---|
| `assert_config_schema_valid(&source)` / `assert_config_schema_valid_value(&sink.config_schema(), sink.connector_name())` | every source / sink |
| `assert_connector_name_nonempty(&source)` / `assert_connector_name_nonempty_value(name, label)` | every source / sink |
| `assert_bounded_memory(&source, batch_size, total)` | every pageable source (`total > batch_size`) |
| `assert_batch_size_zero_single_page(&source)` | sources honoring `batch_size = 0` |
| `assert_bookmark_roundtrip(&source)` | resumable sources |
| `assert_errors_not_panics(&failing_source)` | every source; pass one configured to fail |
| `assert_preflight_check_wellformed(&source, &CheckContext::default())` / `assert_sink_preflight_check_wellformed(&sink, &ctx)` | anything with a `check()` |
| `assert_capabilities_truthful(&sink, distinct_count)` | every sink |
| `assert_idempotent_replay(&sink, distinct_count)` | idempotent or keyed-upsert sinks |
| `assert_write_modes_truthful(&sink, distinct_count)` | sinks advertising `Upsert` / `Delete` |
| `assert_schema_evolution_effective(&sink)` | sinks with `supports_schema_evolution()` |
| `assert_batch_atomicity_declared(&sink)` | sinks overriding `batch_atomicity()` |
| `assert_discover_roundtrips(&source, rebuild)` | sources with `supports_discover()` (integration-level) |
| `assert_cancellation_flushes(&sink, durable_count)` | buffered sinks (integration-level) |

`distinct_count` is a closure returning a future of the destination's current row count: `|| std::future::ready(store.len())` for a fake, or a `SELECT count(*)` for a real database.

Practical points:

- Sink checks write rows keyed on `"id"` with a `"v"` column, so configure the sink under test with `write_mode: upsert` and `key: ["id"]` for the keyed checks.
- Checks measure row-count deltas. Give each check its own fresh destination (a new mock server, table or container); running two keyed checks against the same store makes the second one see no new rows and fail.
- Assert the honest branch. An append-only sink still runs `assert_capabilities_truthful`; it then verifies that `write_batch_idempotent` delegates and that no commit token is reported.
- `faucet_conformance::doubles` has a `CountingSource` and a `TestSink` for testing your own wrappers.

Run everything with:

```bash
cargo test
cargo clippy --all-targets -- -D warnings
cargo fmt --check
```

## The `faucet conformance` command

```bash
faucet conformance --kind source
faucet conformance sqlite
faucet conformance --all --min-tier stable
```

This command scores connectors **compiled into the faucet binary** (built-ins) from static signals: a verified registry entry (40 points), a real config schema (30), a catalog description (10), and capability bonuses. A score of 70 or more is Stable. It does not run your crate's tests, and the stock binary cannot see a third-party crate (`faucet conformance acme` fails with "no connector named 'acme' is compiled into this binary"). For a third-party connector, the runtime battery above is the proof of conformance; you can declare the resulting tier in your registry index entry (see `publishing.md`).
