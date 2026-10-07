---
name: faucet-debug
description: >-
  Use when a faucet pipeline misbehaves: a `faucet run` failed or exited
  non-zero, a run is slow, stuck or behind its source (CDC / stream lag), the
  source is rate-limited or throttled, rows are missing or duplicated, rows
  landed in the dead-letter queue, wrong values landed in the destination, a
  bookmark needs moving or resetting, stored state will not read after an
  upgrade, a scheduled run was skipped, or a connector fails with an auth,
  permission or network error. Covers `faucet doctor`, `status`, `dlq`,
  `state`, `verify`, `rollback`, `profiling`, logs and Prometheus metrics.
license: Apache-2.0
---

# Debugging faucet pipelines

faucet records what you need: per-row status, the bookmark, the dead-letter
queue (DLQ), metrics and logs. Read those first; change state or data only
once you know the cause.

## Before you start

Check that `faucet` is on the `PATH` with `faucet --version`. If it is missing, install it with either:

```bash
# Homebrew (macOS / Linux)
brew install faucet-hq/faucet-stream/faucet-cli

# Installer script (macOS / Linux)
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/faucet-hq/faucet-stream/releases/latest/download/faucet-cli-installer.sh | sh
```

## Golden rules

1. **Read before you write.** `faucet status`, `faucet doctor`,
   `faucet state show`, `faucet dlq inspect`, `faucet explain` and
   `faucet plan` change nothing. Run them first, every time.
2. **Export state before you move it.** Before any `state set`, `state reset`,
   `state import --overwrite` or upgrade, take a backup with
   `faucet state export pipeline.yaml -o state-backup.json`. Use `--dry-run`
   on the mutating command first.
3. **Never `dlq replay` until the cause is fixed.** A replay re-feeds the same
   payloads through the same quality / contract / sink path. If nothing
   changed, they fail again and land in a fresh `replay-failed.jsonl`.
4. **Prefer `verify` before `rollback`.** `faucet verify` tells you exactly
   which keys differ. Often `verify --repair` is the smaller fix. Rollback
   undoes a whole run and rewinds the bookmark.
5. **Know the bookmark invariant.** For every page the order is
   write, flush, then save the bookmark. State is never ahead of the sink, so
   a crash never loses data. It can replay **at most one page**. Under the
   default at-least-once delivery that page may be written twice. Under
   `delivery: exactly_once` the sink's commit token makes the replay a no-op.
6. **Do not edit state files or state tables by hand.** Use `faucet state`.
   It respects run leases and exactly-once watermarks.

## Primitives

| Primitive | What it shows | Reference |
|---|---|---|
| `faucet doctor pipeline.yaml` | Green/red probe per connector: auth, network, permissions, state store, SLA, source lag | [doctor](references/doctor.md) |
| `faucet doctor pipeline.yaml --offline` | Static config lints, no network | [doctor](references/doctor.md) |
| `faucet status pipeline.yaml` | Per row: last success / failure with error kind, bookmark, resume point, lag, DLQ backlog, batch outcomes, SLA, state format, live lease | [status](references/status.md) |
| `faucet status pipeline.yaml --probe` | Also asks the sink watermark, the source lag and overwrite staging, all read-only | [status](references/status.md) |
| `faucet run pipeline.yaml --log-level debug --log-format json` | Verbose structured logs with `pipeline`, `row`, `run_id`, `connector`, error `kind` | [logs and errors](references/logs-and-errors.md) |
| `faucet run pipeline.yaml --output json` | Per-row `status`, `error`, `duration_ms`, `rows_out`, `dlq_count`, `batches`, `source_lag`, `run_id` | [logs and errors](references/logs-and-errors.md) |
| `faucet dlq inspect ./dlq/` | Dead letters grouped by reason and error kind, with samples | [dlq](references/dlq.md) |
| `faucet state show pipeline.yaml` | Each row's bookmark, exactly-once sequence and markers | [state](references/state.md) |
| `faucet state export pipeline.yaml -o state-backup.json` | Full state backup | [state](references/state.md) |
| Prometheus `/metrics` | Per-row duration, lag, throttling, errors by kind, batch outcomes | [metrics](references/metrics.md) |
| `faucet verify pipeline.yaml` | Keys that differ between source and destination, by content | [verify and rollback](references/verify-and-rollback.md) |
| `faucet rollback pipeline.yaml --list` | Runs that can be undone | [verify and rollback](references/verify-and-rollback.md) |
| `faucet profiling show pipeline.yaml` | Column profile and drift findings for the latest run | [profiling and SLA](references/profiling-and-sla.md) |
| `faucet explain pipeline.yaml` | Plain-English summary: delivery mode, state store, matrix rows | (offline) |
| `faucet plan pipeline.yaml --live` | A capped real sample through transforms. No writes, bookmark untouched | (read-only) |

## Exit codes worth knowing

| Command | Exit code |
|---|---|
| `faucet status` | `0` healthy (ok, running, warming), `1` degraded or unknown, `2` failed |
| `faucet doctor` | number of failed probes (clamped to 255); `0` = all passed |
| `faucet verify` | number of differing keys (clamped to 255); `0` = equal |
| `faucet rollback` | conflict count when blocked by later changes |
| `faucet policy`, and `run` / `validate` / `plan` / `doctor` with `--policy` | number of policy violations |
| `faucet backfill`, `faucet test` | number of failed units / cases |
| `faucet run` | `1` when any invocation failed (`N pipeline invocation(s) failed`) |
| `faucet migrate --state --check` | non-zero when some stored key needs migrating |

## Symptom recipes

Replace `pipeline.yaml` and `orders` (a matrix row id) with the real names.
Add `--json` to any read command when you want to parse the output.

### Run exited non-zero

```bash
faucet status pipeline.yaml                 # which row failed, error kind, when
faucet run pipeline.yaml --select orders --log-level debug --output json
```

Look for the row's `last error:` line in status and `rows[].error` in the
JSON summary. The error kind tells you where to go next (see
[logs and errors](references/logs-and-errors.md)): `Auth` / `Http` /
`HttpStatus` go to the auth recipe, `RateLimited` to throttling, `Config` to
`faucet validate`, `SchemaDrift` to schema drift, `StateIncompatible` to
state after upgrade, `CircuitOpen` to the sink being unhealthy.

### Auth, permission or network failure

```bash
faucet doctor pipeline.yaml --timeout-secs 5
faucet validate pipeline.yaml --show-composed  # what the config resolves to before interpolation
```

Look for the `✗` probe and its `hint:` line. A failing source probe means
credentials, DNS / TLS or reachability. A failing sink `auth` probe usually
means a missing dataset, table or grant. A failing `state` probe means the
state backend is unreachable, and no run can save progress. An empty
`${env:VAR}` usually means the variable is unset or `.env` was not loaded
(`--env-file`).

### Slow run

```bash
faucet run pipeline.yaml --output json      # rows[].duration_ms per row
```

Look for the slowest row. In Prometheus compare
`faucet_pipeline_invocation_duration_seconds{status="ok"}` by `row`, then
`faucet_source_page_duration_seconds` against
`faucet_sink_write_duration_seconds` for that row: whichever dominates is the
bottleneck. Check `faucet_source_throttle_wait_seconds` first (time spent
sleeping on rate limits is not a speed problem). Knobs: the sink's
`batch_size` (larger means fewer round trips), `faucet run --concurrency N`
(connector connections / fetches), and `execution.max_concurrent` (matrix rows
in parallel). See [metrics](references/metrics.md).

### Rate-limited or throttled

Look for the run-end warning
`source spent …s of a …s run (…%) waiting on rate limits`, and for
`faucet_source_throttled_total` and `faucet_source_throttle_wait_seconds` by
`row`. Fixes: lower `--concurrency`, stagger schedules so rows do not share a
quota window, or teach the `rest` / `graphql` source the API's reset signal
with `retry_on_response` and `backoff_from`. A `RateLimited` run failure means
retries ran out. Raise `resilience.retry.max_attempts` only if the quota
really does reset.

### CDC or stream lag

```bash
faucet status pipeline.yaml --probe         # lag column, asked fresh from the source
faucet doctor pipeline.yaml                 # `lag` probe; fails when a max_lag_* threshold is exceeded
```

Look for `faucet_source_lag_bytes` (Postgres WAL, MySQL binlog),
`faucet_source_lag_events` (Kafka) or `faucet_source_lag_seconds` rising
across runs. Lag that grows while every run succeeds means the pipeline drains
slower than the source produces: run more often, or speed up the sink (see
slow run). On `postgres-cdc`, lag growing while nothing runs means the slot is
holding WAL. Run the pipeline or drop the slot.

### Rows in the DLQ

```bash
faucet status pipeline.yaml                 # DLQ backlog and oldest envelope per row
faucet dlq inspect ./dlq/ --limit 10
# fix the cause (transform, contract, destination schema), then:
faucet dlq replay pipeline.yaml --from ./dlq/ --dry-run
faucet dlq replay pipeline.yaml --from ./dlq/
faucet dlq discard ./dlq/ --before 7d
```

Look for the `by reason` breakdown: `quality`, `contract`, `schema_drift`,
`partial` (the sink rejected individual rows) or `dlq_all` (a whole batch
failed). Do not replay until the cause is fixed. See [dlq](references/dlq.md).

### Duplicates after a crash or retry

```bash
faucet explain pipeline.yaml                # delivery guarantee and write mode
faucet verify pipeline.yaml                 # reports `duplicate` keys
```

At-least-once delivery may rewrite the one page in flight when the crash
happened. That is expected, not a bug. A replayed `dlq_all` batch on a
`best_effort` sink can also duplicate rows. To make the destination immune,
use `write_mode: upsert` with a `key`, or `delivery: exactly_once`. `verify`
lists each duplicated key and which side holds it. It does not dedupe an
append table for you.

### Missing rows

```bash
faucet status pipeline.yaml                 # bookmark and where the next run resumes
faucet state show pipeline.yaml --row orders
faucet dlq inspect ./dlq/
faucet verify pipeline.yaml --max-differences 50
```

Look for: rows sitting in the DLQ; a bookmark that moved past the data (a
manual `state set`, or a source whose replication key is not monotonic);
`faucet_source_replication_key_missing_total` above zero (a misspelled or
nested `replication_key`); and a `faucet_transform_records_out_total` /
`faucet_transform_records_in_total` ratio below 1 (a filter dropping rows).
`verify` lists `missing in destination` keys. `verify --repair` re-syncs them.

### Wrong values landed

```bash
faucet verify pipeline.yaml                 # which keys and which columns differ
faucet profiling show pipeline.yaml         # null-rate / type / value drift in the latest run
faucet rollback pipeline.yaml --list
faucet rollback pipeline.yaml --run 0199a3f2-example --dry-run
```

If a few keys differ, use `faucet verify pipeline.yaml --repair`. If a whole
run was bad (wrong parameter, bad upstream extract), roll it back. That needs
a `rollback:` block and a SQL sink. Roll back newest first. See
[verify and rollback](references/verify-and-rollback.md).

### Schema drift failure

Error kind `SchemaDrift` (`Schema drift on columns [...]`) comes from
`schema.on_drift: fail` or `on_incompatible: fail`; the counter is
`faucet_schema_drift_total`. Confirm the new shape with
`faucet plan pipeline.yaml --live`, then change the destination or the policy
(`evolve` applies additive changes, `quarantine` sends drifting rows to the
DLQ). Do not reset state for drift: the bookmark is fine.

### State unreadable after an upgrade

```bash
faucet status pipeline.yaml                 # `state format` per row
faucet state export pipeline.yaml -o before-migrate.json
faucet migrate --state pipeline.yaml --check
faucet migrate --state pipeline.yaml
```

`legacy` and `migrate` are fine: the next run, or `migrate --state`, upgrades
them. `incompatible` (error kind `StateIncompatible`) means a newer faucet or
a different source wrote the bookmark. Run the release that wrote it, or
restore an older export. Reset only after you have decided where the row
should resume. See [state](references/state.md).

### Scheduled run skipped

Look for `faucet_schedule_overlaps_total{policy="skip"}` rising: a tick fired
while the previous run was still going and `overlap_policy: skip` dropped it.
Compare `faucet_schedule_last_run_duration_seconds` with the cron period. Fix:
a faster run, a wider period, or `overlap_policy: queue` (one missed tick runs
right after). `forbid` exits the scheduler non-zero on overlap. A
`CircuitOpen` failure delays the next tick by `circuit_breaker.cooldown_secs`.

### A bookmark needs moving or resetting

```bash
faucet state export pipeline.yaml -o state-backup.json
faucet state set pipeline.yaml --row orders --bookmark '{"updated_at":"2026-09-22T00:00:00Z"}' --dry-run
faucet state set pipeline.yaml --row orders --bookmark '{"updated_at":"2026-09-22T00:00:00Z"}' --yes
faucet state reset pipeline.yaml --row orders --dry-run
```

Both commands refuse while a run holds the row's lease. Use `--force` only
when you know that run is gone. Moving a bookmark backwards re-reads data, so
an append sink gets duplicates. Moving it forwards skips data. See
[state](references/state.md).
