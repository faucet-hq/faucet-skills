# State, dead-letter queues and delivery guarantees

## State stores

```yaml
state:
  type: file                 # memory | file | redis | postgres
  config:
    path: ./.faucet-state
```

| Backend | Config | Use when |
|---|---|---|
| `memory` | none | Tests and one-shot runs. Lost at exit; rejected by exactly-once, `mirror`, `rollback`. |
| `file` | `path`, optional `encryption: { key }` | One host. One JSON file per key, atomic writes. |
| `redis` | `url`, `namespace` | Shared across hosts. |
| `postgres` | `url`, `table` (default `faucet_state`), `ensure_table`, `max_connections` | Shared, durable, transactional. |

- Without a `state:` block nothing is remembered between runs: incremental
  sources start over, `file` incremental re-reads every file, and SLA
  staleness / profiling baselines cannot work.
- The bookmark is persisted only after the sink confirms the page, so a crash
  re-reads from the last confirmed point; it never skips data.
- State keys are `{name}::{row_id}` (DAG children add the parent key). Changing
  `name` or a matrix row `id` starts a fresh bookmark.
- Put state on storage that outlives the process (a volume, not a container's
  scratch disk). Secrets in `encryption.key` use the usual references.

Inspect and operate:

```bash
faucet state show pipeline.yaml               # every row's bookmark and markers
faucet state set pipeline.yaml --row orders --bookmark '"2026-06-01T00:00:00Z"' --dry-run
faucet state reset pipeline.yaml --row orders --dry-run   # next run re-syncs that row
faucet state export pipeline.yaml             # backup; `faucet state import` restores
faucet status pipeline.yaml                   # health per row; exit 0 healthy, 1 degraded/unknown, 2 failed
```

## Dead-letter queue

```yaml
dlq:
  sink:
    type: file
    config: { path: ./dlq/orders.jsonl }
  on_batch_error: propagate      # propagate (default) | dlq_all
  max_failures_per_page: 50      # fail the run past this many DLQ rows in one page
  max_failures_total: 500        # ... or in the whole run
```

- Receives rows quarantined by quality, contract, drift or policy checks,
  rows a sink rejected individually (`bigquery`, `elasticsearch`, `http` in
  individual mode), and rows with a missing upsert key.
- A DLQ always appends and holds the only copy of those rows (the bookmark
  moves past them). Do not point it at a data sink's path.
- `dlq_all` sends a whole failed batch to the DLQ. It is refused on sinks whose
  failed write may have landed part of the batch, unless the sink writes by
  key (`write_mode: upsert`) or you set `allow_duplicates_on_dlq_all: true`.
- Set the failure budgets so a broken upstream stops the run instead of
  filling the DLQ.

Work the DLQ:

```bash
faucet dlq inspect ./dlq/orders.jsonl                        # counts by reason / error, sample
faucet dlq replay pipeline.yaml --from ./dlq/orders.jsonl --dry-run
faucet dlq replay pipeline.yaml --from ./dlq/orders.jsonl    # re-runs quality + contract, skips transforms + masking
faucet dlq discard ./dlq/orders.jsonl --reason contract --before 7d
```

Replays are fresh writes; make the target `upsert` so a replay cannot duplicate.

## Delivery guarantees

`faucet validate` prints the guarantee each row actually gets:

| Printed | Meaning |
|---|---|
| `at-least-once` | A crash between sink write and bookmark save re-delivers that page. Downstream must tolerate duplicates. |
| `effectively-once (keyed upsert)` | Sink writes by key, so replays converge. Works with any source; DLQ allowed. |
| `effectively-once (atomic watermark)` | Sink commits each page and a commit token in one transaction; resume reads the token. |

`delivery: exactly_once` makes validate reject the config unless one of the
two effectively-once mechanisms applies:

- Keyed upsert: `write_mode: upsert` (or `delete`) with a `key` on an
  upsert-capable sink.
- Atomic watermark, all four: a positional-replay source (`postgres-cdc`,
  `mysql-cdc`, `mssql-cdc`, `mongodb-cdc`, `kafka`); a sink that commits a
  watermark (`postgres`, `mysql`, `mssql`, `sqlite`, `bigquery`, `snowflake`,
  `iceberg`, `kafka`, `redis`, `mongodb` (replica set), `spanner`,
  `databricks`); a durable `state:` (not `memory`); no `dlq:` block.

Not allowed with `exactly_once`: `write_mode: overwrite`, `complete_for`
cleanup, and any quarantine policy on the atomic-watermark path.
`faucet backfill` always runs at-least-once per window; pair it with upsert.

## Retries and resilience

```yaml
resilience:
  retry: { max_attempts: 5, backoff: exponential, base_ms: 200, max_ms: 30000, jitter: true }
  retry_on: [http_5xx, rate_limited, connection, timeout]
  circuit_breaker: { consecutive_failures: 5, cooldown_secs: 60 }
  poison: { max_row_attempts: 3, action: dlq }   # dlq needs a dlq: block; or drop | fail
```

Without this block sink writes are not retried. On `rest`, explicit
`max_retries` / `retry_backoff` in the source config take precedence.
