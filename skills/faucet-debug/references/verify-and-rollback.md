# Verify and rollback

Use `verify` to find out exactly what is wrong in the destination. Use
`rollback` only when a whole run should not have happened. Verify first.

## faucet verify

Compares source and destination **by content**, keyed on the sink's upsert
`key` or `verify.key`. A keyless destination is refused.

```bash
faucet verify pipeline.yaml                          # exit code = differing keys (0 = equal)
faucet verify pipeline.yaml --row orders --json
faucet verify pipeline.yaml --max-differences 50     # cap the report; the count keeps going
faucet verify pipeline.yaml --repair --dry-run       # plan the repair
faucet verify pipeline.yaml --repair                 # re-sync missing and changed keys
faucet verify pipeline.yaml --repair --allow-delete  # also delete rows only the destination has
```

```text
verify [orders]: DIFFERENT — sqlite:///app.db#orders vs sqlite:///mirror.db#orders (key id, range mode, 21 range(s) compared, 3 differing)
  3 differing key(s): 1 missing in destination, 1 extra in destination, 1 changed, 0 duplicated
    {"id":2} → changed: amount
    {"id":3} → missing in destination
    {"id":9} → extra in destination
```

| Difference | Usually means |
|---|---|
| `missing in destination` | Rows in the DLQ, a bookmark moved past them, a filter, or a missed change event |
| `extra in destination` | Source deletes not propagated, or hand-inserted rows |
| `changed: <columns>` | Missed update, hand edit, or a transform / mask change since load |
| `duplicate` | Key appears more than once on one side (at-least-once replay, DLQ replay on an append sink) |

How it reads:

- The source side goes through the row's transforms and masking first, so it
  compares what the pipeline would write. `_faucet_*` metadata columns are
  excluded by default.
- With a single integer key on SQL sources (`postgres`, `mysql`, `sqlite`,
  `mssql`), it digests key ranges and bisects only the ranges that differ.
  Other shapes compare the whole dataset in one keyed pass, bounded by
  `verify.max_rows_scanned` (the report is then marked `truncated`).
- Non-SQL sinks need `verify.destination` to say how to read the
  destination back.
- A source changing during the scan can show up as a difference. Re-run.

`--repair` writes through the row's own sink with `write_mode: upsert`, so
quality, contract and DLQ still apply. The sink must support keyed writes.
`--allow-delete` deletions cannot be undone. A second `verify` after a repair
should report zero.

Refused on `write_mode: overwrite` rows and on rows that share a destination
with other rows (partition chunks, several matrix rows on one table).

## faucet rollback

Undoes exactly what one run wrote, then rewinds the row's bookmark (and
exactly-once commit token) so the next run re-reads that window.

Requires a `rollback:` block, a durable `state:` store, and a SQL sink in
column mode (`postgres`, `sqlite`, `mysql`). Only the last `retain` runs per
row (default 10) can be undone. Fan-out child rows and DLQ'd rows are not
touched.

```bash
faucet rollback pipeline.yaml --list
faucet rollback pipeline.yaml --run 0199a3f2-example --dry-run
faucet rollback pipeline.yaml --run 0199a3f2-example --row orders
faucet rollback pipeline.yaml --run 0199a3f2-example --json
```

The run id is printed by `faucet run` per row, is `rows[].run_id` in
`faucet run --output json`, and is the `_faucet_run_id` column value.

| Run wrote with | Undo |
|---|---|
| `append` | Deletes rows where `_faucet_run_id` is that run |
| `upsert` / `delete` | Deletes keys the run created, restores journaled before-images of keys it changed or deleted |
| `overwrite` | Swaps the kept `<table>__faucet_prev` back in |

### When it is blocked

The command exits with the conflict count and changes nothing when:

- **A later run changed some of the same keys.** Restoring them would
  overwrite newer data.
- **The run is not the newest.** Rewinding the bookmark would make the next
  run re-read the later runs too. Roll back newest first.

`--force` restores conflicting keys anyway, or (for an older run) undoes only
that run's rows and leaves the bookmark where later runs put it. Read the
`--dry-run` output before using it.

### After a rollback

```bash
faucet verify pipeline.yaml
faucet status pipeline.yaml --row orders
```

The next normal run re-reads the undone window. Fix the cause first (bad
parameter, broken upstream extract), or it will load the same bad data again.
