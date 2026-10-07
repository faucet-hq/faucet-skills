# faucet status

`faucet status` prints one screen of health per matrix row, read from the
pipeline's `state:` store. It runs nothing and writes nothing.

```bash
faucet status pipeline.yaml
faucet status pipeline.yaml --row orders
faucet status pipeline.yaml --probe        # also read sink watermark, source lag, overwrite staging (read-only)
faucet status pipeline.yaml --json
```

Exit code: **0** healthy (`ok`, `running`, `warming`), **1** degraded or
unknown, **2** failed. That makes it a cron or monitoring check on its own.

## Example

```text
pipeline orders (3 rows) — FAILED    state: file
  row        status    last success            bookmark               lag  dlq  next run resumes at
  customers  ok        2026-09-26 06:10 (2h)   updated_at=2026-09-26  —    0    updated_at=2026-09-26
  orders     FAILED    2026-09-25 23:00 (9h)   lsn=0/3A00F128         412 MiB 17   lsn=0/3A00F128
             └ last error: Sink: deadlock detected (2026-09-26 02:14, run 01a0…)
             └ SLA staleness: last success 32400s ago exceeds max_staleness_secs 21600
             └ DLQ: 17 record(s), oldest 2026-09-26 02:14 (6h)
  refunds    warming   never                   —                      —    0    full snapshot
```

## Fields and what to do with them

| Field | Meaning | Next step |
|---|---|---|
| last success / last failure | Time, run id, and for failures the error kind plus redacted message | Error kind → [logs and errors](logs-and-errors.md) |
| bookmark, next run resumes at | Where the next run starts; `full snapshot` when there is no bookmark | `faucet state show` for the raw value |
| exactly-once | Committed sequence. With `--probe`: `Agree`, `SinkAhead` (crash between sink commit and state write; next run re-anchors to the sink, no action), `StateAhead` (the sink lost or rewound pages, degraded), `NoToken` | `StateAhead` → `faucet verify` |
| lag | How far the source is behind its head (bytes, events or seconds) at the end of the last run. `--probe` asks again now | See the lag recipe in SKILL.md |
| dlq | Envelopes for this row in a local JSON Lines DLQ, with the oldest. Other DLQ sinks are noted as not countable | [dlq](dlq.md) |
| batches | How the last run's sink writes ended: committed, some rows to DLQ, whole writes to DLQ (`dlq_all`), or failed | Any DLQ outcome marks the row degraded |
| SLA | Staleness, `min_rows_per_run`, volume baseline | [profiling and SLA](profiling-and-sla.md) |
| profiling | Column drift in the latest run | `faucet profiling show` |
| rollback | Undoable runs | `faucet rollback --list` |
| overwrite staging | With `--probe`: `present` means a crashed overwrite left its `…__faucet_ovw` staging table; the next overwrite replaces it | Re-run |
| running | A live run lease (pid, host, since). An **expired** lease means the run stopped without releasing it, probably a crash | Check the host; the next run clears it |
| state format | `current`, `legacy`, `migrate` (fine: next run or `faucet migrate --state` upgrades), `incompatible` (refused) | [state](state.md) |
| children | Child rows fold under their parent: bookmark count, failed invocations, worst health | `--row` the child id |

Every field is read independently. An unreachable state backend, history
store or DLQ shows up as a note on the row, not as a command failure. A
`memory` state store always reports `unknown`: nothing survives between runs.

Preview runs (`--dry-run`, `--limit`) do not write the status marker, so
they never show up here.
