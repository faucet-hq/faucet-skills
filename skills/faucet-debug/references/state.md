# Pipeline state

The `state:` store keeps, per matrix row, the bookmark (where the next run
resumes) plus markers: run outcome (`::__status__`), live run lease
(`::__lease__`), SLA history, profiling baseline, rollback markers, and
pipeline-level replication / backfill markers.

## The invariant

For every page: **write, flush, then save the bookmark.** The stored
bookmark is never ahead of what the sink holds durably.

- A crash before the flush: the bookmark did not move; the page is re-read.
- A crash after the flush but before the save: the sink has the page, state
  is one page behind; the page is re-read.

So a crash never loses data and replays **at most one page**. Under
at-least-once delivery that page can be written twice. Under
`delivery: exactly_once` the sink's commit token holds the committed
sequence and an embedded bookmark. On resume the higher of state and sink
wins, so the replay writes nothing.

Retries never move the bookmark. Preview runs (`--dry-run`, `--limit`) and
`memory` stores keep no durable state.

## Show (read-only)

```bash
faucet state show pipeline.yaml
faucet state show pipeline.yaml --row orders --json
```

Each row prints its bookmark, the exactly-once sequence, a `state format`
line (owner, schema, status) and every marker.

## Safety rules

1. **Export first.** Always:

   ```bash
   faucet state export pipeline.yaml -o state-backup.json
   ```

2. **Dry-run first.** `set`, `reset` and `import` all take `--dry-run` and
   print before / after.
3. **Never under a running pipeline.** These commands refuse while a row's
   lease is live, or while the config's `catalog:` store lists a run in flight.
   `--force` overrides that only when you know the run is gone (for example,
   an expired lease after a crash).
4. Without a terminal, `set`, `reset` and `import` refuse unless `--yes` is
   given, so scripts never change state by accident.
5. Do not hand-edit state files, keys or tables.

## Move a bookmark

```bash
faucet state set pipeline.yaml --row orders --bookmark '{"updated_at":"2026-09-22T00:00:00Z"}' --dry-run
faucet state set pipeline.yaml --row orders --bookmark '{"updated_at":"2026-09-22T00:00:00Z"}' --yes
faucet state set pipeline.yaml --row lines --parent-key 42 --bookmark '{"id":1000}' --yes
```

The bookmark is JSON in the shape the source uses. Copy the shape from
`faucet state show --json`. Moving backwards re-reads data (an append sink
gets duplicates; an upsert sink overwrites). Moving forwards skips data
permanently.

On an exactly-once row, `set` keeps the envelope and raises the sequence to
the sink's watermark, so the new position is honoured. If the watermark
cannot be read, it refuses. `--skip-watermark-check` writes anyway; the next
run may then re-anchor to the sink's position instead of your bookmark.

## Reset a row

```bash
faucet state reset pipeline.yaml --row refunds --dry-run
faucet state reset pipeline.yaml --row refunds --yes                     # next run re-syncs from the start
faucet state reset pipeline.yaml --row refunds --include-markers --yes   # also forget SLA / profiling baselines, run outcomes, rollback markers
faucet state reset pipeline.yaml --row lines --parent-key 42 --yes       # one child invocation
faucet state reset pipeline.yaml --row orders --rewind-token --yes       # exactly-once: also delete the sink's commit token
```

A reset means a full re-sync. On an append sink that duplicates everything
already written. Plan for `write_mode: overwrite` or `upsert` before you
reset.

## Backup, restore, move backends

```bash
faucet state export pipeline.yaml -o state-backup.json
faucet state import pipeline.yaml state-backup.json --dry-run
faucet state import pipeline.yaml state-backup.json --yes
faucet state import pipeline.yaml state-backup.json --overwrite --yes
faucet state import pipeline.yaml state-backup.json --to-state postgres://faucet@db/faucet --yes
```

- The export is versioned JSON (`{version, pipeline, exported_at, keys}`)
  with every key as stored, except run leases.
- `import` refuses a namespace that already holds state unless
  `--overwrite`, which deletes keys absent from the export. It also refuses an
  export for another pipeline, and a newer export version.
- `--to-state` accepts `postgres://…`, `redis://…`, `file:DIR`, `memory`, or a
  `{type, config}` document. After moving, point the config's `state:` block
  at the new store.

## After an upgrade

Each bookmark is stored in an envelope naming its owner source and schema
version. `faucet status` and `faucet state show` report the format:

| Format | Meaning | Action |
|---|---|---|
| `current` | Readable as-is | none |
| `legacy` | Written before versioning | none; the next run rewrites it |
| `migrate` | Older schema | none, or migrate ahead of the run |
| `incompatible` | Newer schema, newer envelope, or another source | Run fails with `StateIncompatible` before reading the source |

```bash
faucet state export pipeline.yaml -o before-upgrade.json
faucet migrate --state pipeline.yaml --check     # non-zero if any key needs work
faucet migrate --state pipeline.yaml --row orders --json
faucet migrate --state pipeline.yaml
```

For `incompatible`: you probably downgraded, or a row's `type:` changed.
Either run the release that wrote the state, or restore the export taken
before the upgrade:

```bash
faucet state import pipeline.yaml before-upgrade.json --overwrite --yes
```

Reset the row only after you have decided where it should resume.
