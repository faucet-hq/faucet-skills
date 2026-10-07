# Parallel run, verification and cutover

The old pipeline stays authoritative until faucet has produced the same data
for long enough to trust it. Nothing old is deleted until the user has signed
off on the comparison.

## 1. Parallel run into a separate dataset

- Point every faucet sink at a **separate schema / dataset** (`raw_faucet`,
  `analytics_faucet`), never at the tables the old pipeline writes. Two
  writers on one table make every comparison meaningless.
- Give faucet its **own state store** (a `file` path or a `postgres` / `redis`
  namespace nobody else uses).
- Do a full initial load (or a backfill over the same history the old tables
  hold), then let faucet run on its schedule next to the old one for at least
  a few cycles, including the busiest period of the day or week.
- Use profiles or a separate file for the parallel run so the final config
  differs only in the sink target:
  `faucet run pipeline.yaml --profile parallel`.

## 2. Compare with `faucet verify`

`faucet verify` compares a config's source with its sink **by content**, keyed
on the sink's `key` (or `verify.key`). Exit code = number of differing keys.
Two comparisons answer different questions:

1. **faucet vs the source system**: `faucet verify pipeline.yaml --row <row>`
   on the real pipeline config. Proves faucet loaded what the source holds.
   Not meaningful on a row whose query is a `${now.*}` window (everything
   outside the window shows as "extra"), on `write_mode: overwrite` rows, or
   on rows that share a destination table.
2. **faucet vs the old pipeline's output**: a comparison-only config whose
   source reads the old table and whose sink is faucet's table
   ([examples/verify-meltano-vs-faucet.yaml](../examples/verify-meltano-vs-faucet.yaml)).
   **Never `faucet run` that file**: it would copy old rows into faucet's
   table.

```bash
faucet verify verify-meltano-vs-faucet.yaml --row customers
faucet verify verify-meltano-vs-faucet.yaml --row customers --json > customers-verify.json
faucet verify verify-meltano-vs-faucet.yaml --row customers --max-differences 50
```

Making the comparison fair:

- `verify.exclude` the metadata columns of both tools (`_sdc_*`,
  `_airbyte_*`, `_faucet_*`) and any column the two pipelines compute
  differently on purpose (a different hash function, a renamed field).
  `verify.columns` lists exactly what to compare instead.
- `verify.normalize` handles timestamps (on by default), float tolerance and
  numeric strings, since Singer targets and faucet may store types
  differently.
- Read during a quiet window, or pause both schedules: rows that change during
  the scan are reported as differences. Re-run before chasing them.
- Postgres / MySQL / SQLite / MSSQL with a single integer key compare by
  range digests and bisect; other shapes compare the full dataset in one pass
  (cap it with `verify.max_rows_scanned`). Any sink other than SQL in column
  mode needs `verify.destination` to say how to read it back.

Reading the result:

| Difference | Likely cause during a migration |
|---|---|
| missing in destination | faucet's window or backfill did not cover it, a filter differs, a row went to faucet's DLQ |
| extra in destination | the old pipeline never propagated a delete, or the old selection was narrower |
| changed: `<columns>` | type or format difference (normalize it), a transform difference, or a late update one side has not picked up yet |
| duplicate | an append-only side replayed a page; use upsert |

Explain every difference class to the user before cutover. Do not use
`--repair` on the comparison-only config; it would write old-pipeline rows into
faucet's table. `--repair` on the real pipeline config re-syncs differing
keys from the source and is fine once the cause is understood.

For row counts only, the cheaper `reconcile:` block exists; it cannot see a
row that differs in value.

## 3. Cut over

Pick one, and write it down with the user:

**A. Switch readers to faucet's dataset.** Repoint dbt sources, views or
dashboards at `raw_faucet`, then stop the old schedule. Simple and instantly
reversible (repoint back), but every reader must move.

**B. Faucet takes over the original tables.** Stop the old schedule, read
its final bookmark, carry it over ([state-carryover.md](state-carryover.md)),
change the faucet sink to the original tables, `faucet validate`, run once,
`faucet verify`, then enable `faucet schedule`. Readers do not move, but table
shapes must match what they expect (column names, types, metadata columns).

In both cases: export faucet state right after the first production run
(`faucet state export pipeline.yaml -o state-after-cutover.json`) and watch
`faucet status pipeline.yaml` for the first few scheduled runs.

## 4. Keep a rollback path

Until the user signs off (typically after a full business cycle):

- **Do not delete** the old project, `meltano.yml`, plugin environments,
  Meltano state, Airbyte connections, or the old tables. Disable schedules;
  do not uninstall.
- Keep the old pipeline runnable: rollback = disable `faucet schedule`,
  re-enable the old schedule, repoint readers (option A) or let the old
  pipeline resume from its own, untouched state (option B; it re-reads from
  its last bookmark, so pair with a keyed target).
- Keep faucet's state exports so a faucet-side mistake can be undone with
  `faucet state import ... --overwrite`.
- If a faucet run loaded bad data into a SQL sink with a `rollback:` block,
  `faucet rollback` can undo that run; see the `faucet-debug` skill.
- For a Postgres `LOG_BASED` tap, drop its replication slot only after
  sign-off; until then it holds WAL, so watch disk on the source.

Only after sign-off: remove the old schedules, slots, plugin installs and
tables, in that order, each with the user's explicit go-ahead.
