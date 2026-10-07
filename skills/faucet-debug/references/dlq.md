# Dead-letter queue

A `dlq:` block routes rows that fail to a separate sink, so the rest of the
page commits and the bookmark moves on. Once a row is dead-lettered, the DLQ
holds its **only copy**. A DLQ always appends, never truncates.

## Inspect (read-only)

```bash
faucet dlq inspect ./dlq/
faucet dlq inspect ./dlq/dead-letters.jsonl --reason contract --limit 20
faucet dlq inspect './dlq/*.jsonl' --json
faucet dlq inspect ./dlq/ --encryption-key "$DLQ_KEY"
```

The location is a `.jsonl` file, a directory of `*.jsonl` files, or a glob.

```text
DLQ inspect: ./dlq/contract_breaches.jsonl
  files read: 1   envelopes: 42   malformed: 0   non-envelope: 0
  by reason:
    contract       42
  by error kind:
    ContractViolation    42
  sample (5 of 42):
    [contract/ContractViolation] status: value not in enum
      {"order_id":"A-17","status":"backordered"}
```

## Reasons and what they mean

| `reason` | Stage that rejected the row | Typical fix |
|---|---|---|
| `quality` | A quality check with a quarantine policy | Fix upstream data, or the check |
| `contract` | The data contract | Fix the record or bump the contract |
| `schema_drift` | `schema.on_drift: quarantine`, or `on_incompatible: quarantine` | Evolve the destination schema |
| `partial` | The sink rejected individual rows (per-row results) | Read `error.message`: type, constraint or size errors |
| `dlq_all` | A whole batch failed under `on_batch_error: dlq_all` | Fix the sink-side cause (connectivity, permissions, schema) |

Each envelope holds `error.kind`, `error.message`, `reason`, `payload` (the
record after transforms and masking), `ts_ms`, `sink`, `pipeline`, `row` and
`record_index`.

Sealed (encrypted) lines without a key are counted as encrypted, never as
malformed.

## Replay, only after the cause is fixed

```bash
faucet dlq replay pipeline.yaml --from ./dlq/ --dry-run
faucet dlq replay pipeline.yaml --from ./dlq/ --reason contract
faucet dlq replay pipeline.yaml --from ./dlq/ --row orders --failed-dlq ./dlq/retry-2.jsonl
```

- The replay re-feeds `payload` through quality, contract and the sink. It
  **skips transforms and masking**, because the payload already passed
  through both and they are not idempotent.
- Rows that fail again go to a fresh DLQ (default: a `replay-failed.jsonl`
  sibling), never back into the source file. A replay cannot loop.
- A replay is a fresh write. On an append-only sink, rows that partly landed
  before the original failure can be duplicated. Prefer `write_mode: upsert`
  on the target.
- The replay picks up the config's own DLQ `encryption` key automatically.

## Discard handled envelopes

```bash
faucet dlq discard ./dlq/ --reason contract --before 7d
faucet dlq discard ./dlq/dead-letters.jsonl --before 2026-09-01T00:00:00Z --delete
```

By default envelopes move to a `<file>.archived` sibling, which later
`inspect` and `replay` of a directory skip. `--delete` removes them for good.

## Batch atomicity and duplicates

`on_batch_error: dlq_all` sends every row of a failed write to the DLQ. That
is only safe if the failed write landed nothing. Each sink declares its batch
atomicity:

| Atomicity | A failed write | `dlq_all` |
|---|---|---|
| `atomic` | landed nothing | allowed |
| `per_row` | reports which rows failed | allowed |
| `best_effort` | may have landed some rows | refused unless `allow_duplicates_on_dlq_all: true` |

If someone set `allow_duplicates_on_dlq_all: true`, a replay can write rows
twice. That explains duplicates seen after a replay.

## Budgets and signals

- `max_failures_per_page` / `max_failures_total` abort the run when crossed,
  after the crossing page commits. A run that aborts with many DLQ rows means
  something upstream broke. Inspect before re-running.
- Metrics: `faucet_sink_dlq_records_total`, `faucet_sink_dlq_budget_exceeded_total`,
  `faucet_batch_outcomes_total{outcome="dlq_partial"|"dlq_all"}`.
- `faucet status` shows the DLQ backlog per row for local JSON Lines DLQs and
  marks a row degraded when its last run sent writes to the DLQ.
