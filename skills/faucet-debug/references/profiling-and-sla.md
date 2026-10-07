# Profiling and SLA

Both blocks catch silent failures: runs that succeed but write the wrong
amount or the wrong shape of data. Both keep their history in the `state:`
store next to the bookmarks.

## SLA (`sla:` block)

An SLA violation never fails a run. It logs a `WARN` with `pipeline`, `row`
and `kind`, increments `faucet_pipeline_sla_violations_total{kind}`, and
shows up in `faucet status` and `faucet doctor`.

| Check | Fires when | `kind` |
|---|---|---|
| `max_staleness_secs` | A run fails and the last success is older than the threshold | `staleness` |
| `min_rows_per_run` | A run succeeds but writes fewer rows | `min_rows` |
| `volume_anomaly` | A successful run's volume is anomalous versus recent runs (z-score or IQR) | `volume` |
| `max_lag_bytes` / `max_lag_events` / `max_lag_seconds` | The source is further behind its head than the threshold at run end | `lag` |

```bash
faucet status pipeline.yaml        # SLA line per row
faucet doctor pipeline.yaml        # staleness / baseline / lag probes; non-zero when stale
```

Reading it:

- `staleness`: the pipeline has been failing for a while. Go to the
  failed-run recipe.
- `min_rows` or `volume` low: the source returned less than usual. Check the
  bookmark (`faucet state show`), source filters, and
  `faucet_source_replication_key_missing_total`. A drop to zero after a
  bookmark move usually means it moved too far forward.
- `volume` high: a backfill, a reset bookmark, or an upstream duplicate. Check
  for a recent `state set` / `state reset`.
- Baseline `skip: warming up 2/5`: not enough history yet. Normal.
- `--dry-run` and `--limit` runs never touch the baseline. Neither do failed
  runs.

## Profiling (`profiling:` block)

Every run profiles the columns it wrote (null rate, types, distinct count,
numeric and string-length stats, top values) and compares them with a rolling
baseline. Detection starts after `min_history` runs.

```bash
faucet profiling show pipeline.yaml
faucet profiling show pipeline.yaml --row invoices --full
faucet profiling show pipeline.yaml --json
```

```text
row row-0 — 6 run(s) in the baseline (state orders::row-0)
  latest run 0199a3… at 2026-09-25T02:00:04Z: 12,480 row(s), 4 column(s)
  drift: 2 finding(s)
    ! amount.null_rate: null_rate 0.4000 vs baseline mean 0.0012 — |z| 41.30 exceeds 3 (…)
    ! region.new_value: value "latam" is 12.5% of the run; never among the top values in 6 baseline runs
```

| Finding | Usually means |
|---|---|
| `null_rate` up | Upstream stopped sending a field, or a renamed field |
| `type_mix` | Strings in a numeric column: upstream format change |
| `mean` / `min` / `max` shift | Unit change (cents vs units), bad join, test data |
| `new_value` / `vanished_value` | An enum gained or lost a value |
| `psi` | The value distribution moved |
| `distinct` | A column's cardinality changed: compare with the source |

Under `on_drift: fail` the run fails with `ProfileDrift` **after** the data
was written. The failure marks the run; it does not undo it. If the data is
wrong, follow the wrong-values recipe (`faucet verify`, then
`faucet rollback`).

When the change is legitimate (a planned migration), re-baseline so the next
`min_history` runs learn the new normal:

```bash
faucet profiling reset pipeline.yaml --column amount
faucet profiling reset pipeline.yaml --row invoices
```

Metrics: `faucet_profile_drift_total{pipeline,row,column,metric}`,
`faucet_profile_runs_total{outcome}` (`stable`, `drifted`, `warming`),
`faucet_profile_baseline_runs`.

Added or removed columns are not profiling findings. Schema drift handles
those (`schema.on_drift`, `faucet_schema_drift_total`).
