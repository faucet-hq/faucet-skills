# Carrying incremental state over

Goal: the first faucet run continues where Meltano / Singer / Airbyte stopped,
instead of re-reading all history. Do this only for streams that will write
into the destination the old pipeline already filled. A parallel run into a
fresh dataset (see [cutover.md](cutover.md)) usually wants a full initial load
instead, so `faucet verify` can compare complete tables.

## Rules

1. **Back up first.** Export faucet state before any `state set` or
   `state import`, even when the store is new (the export then documents that
   it was empty):
   ```bash
   faucet state export pipeline.yaml -o state-before-migration.json
   ```
2. **Dry-run every change** and read the before / after it prints.
3. **Stop the old pipeline's schedule for that stream first**, then read its
   bookmark, then start faucet. A bookmark read while the old pipeline keeps
   running is already stale.
4. **Never hand-edit state files or state tables.** `faucet state set` writes
   the versioned envelope the source expects (`{"faucet_state": 1, "owner":
   "<source>", "schema": N, "data": ...}`) and respects run leases.
5. **Prefer a bookmark slightly behind** the old one over one ahead of it.
   Behind re-reads a little (harmless with upsert); ahead silently skips rows.

## Find the row id

Bookmarks are stored per row under `<pipeline name>::<row>`. A single-row
config's row is `row-0`; a matrix row's is its `id`; a hub template row is
the stream name under the template id (`faucet-hq/github::issues`).
`faucet validate` lists the rows; `faucet state set` with a wrong `--row`
fails and names the valid ones.

## By source type

### `singer` source (bridge): copy the Singer state as-is

The bookmark **is** the tap's STATE value, passed back to the tap as
`--state`. Take the inner `singer_state` object from `meltano state get
<state_id>` (or the plain Singer `state.json`) and set it unchanged:

```bash
faucet state export erp.yaml -o erp-state-backup.json
faucet state set erp.yaml --row row-0 --dry-run \
  --bookmark '{"bookmarks":{"invoices":{"replication_key":"updated_at","replication_key_value":"2026-10-06T23:58:12Z"}}}'
faucet state set erp.yaml --row row-0 --yes \
  --bookmark '{"bookmarks":{"invoices":{"replication_key":"updated_at","replication_key_value":"2026-10-06T23:58:12Z"}}}'
faucet state show erp.yaml
```

The bridge runs one stream per row, so the same Singer state object can be
set on each stream's row; the tap reads only its own stream's entry.

### `rest` / `graphql`: the bookmark is the replication-key value

The stored bookmark is the bare value of `replication_key` (the newest one
seen), not a Singer `bookmarks` object. Two ways in:

- **No faucet state yet:** put the old value in `start_replication_value`.
  It is used only while the row has no stored bookmark, so it is safe to leave
  in the config afterwards.
- **State already exists** (for example after a test run): move it.
  ```bash
  faucet state set tickets.yaml --row row-0 --dry-run --bookmark '"2026-10-06T23:58:12Z"'
  faucet state set tickets.yaml --row row-0 --yes --bookmark '"2026-10-06T23:58:12Z"'
  ```
  Note the JSON quoting: a timestamp bookmark is a JSON string.

Translate the value if the tap stored it differently from how the record
carries it (epoch seconds vs RFC 3339, a different field). A `rest` source
with `window:` or `persist_cursor` stores a different shape: run a copy of
the config whose `state:` points at a scratch directory, read the shape with
`faucet state show` on that copy, then set the real row.

### SQL sources with a `replication:` block (`mssql`, `redshift`, `clickhouse`, `spanner`, `databricks`)

With no faucet state yet, set `replication.initial_value` to the old bookmark.
Otherwise use `faucet state set` with a bookmark shaped like the one
`faucet state show` prints for that row after a scratch run.

### `postgres`, `mysql`, `snowflake`, `bigquery`, `mongodb` query sources: nothing to carry

These sources store no bookmark (faucet-hq/faucet-stream#825 for postgres and
mysql). A Meltano `INCREMENTAL` stream becomes a `${now.*}` window plus
`write_mode: upsert`, and the old bookmark becomes the **start of a backfill**
that closes the gap between Meltano's last run and faucet's first scheduled
window:

```bash
faucet backfill app-db-to-postgres.yaml --row customers --from 2026-10-06 --to 2026-10-08 --dry-run
faucet backfill app-db-to-postgres.yaml --row customers --from 2026-10-06 --to 2026-10-08
```

Mind how the query uses the clock. In
[examples/app-db-to-postgres.yaml](../examples/app-db-to-postgres.yaml) a
window starting on day D reads rows updated on D-1, so `--from` is one day
after the bookmark's date. Pick a range that overlaps the old bookmark; upsert
makes the overlap harmless. Deletes are never seen by a query source; if the
old stream was `LOG_BASED` or deletes matter, use `postgres-cdc` / `mysql-cdc`
or `faucet mirror` instead, which take their own snapshot and log position
(nothing to carry over).

Several backfill windows into a table that does not exist yet can race on
creating it (a window fails with a duplicate `CREATE SCHEMA`). Create the
schema first, or run the first window alone; `faucet backfill ... --resume`
retries failed windows.

### CDC sources

`postgres-cdc`, `mysql-cdc`, `mongodb-cdc`, `mssql-cdc` store their own log
position. Do not try to reuse a Singer `LOG_BASED` bookmark (an LSN or
binlog position) or the tap's replication slot: give faucet its own slot and
start with `faucet mirror` (snapshot, then stream from a position captured
before the snapshot). After cutover, drop the tap's Postgres slot; an unread
slot retains WAL on the source until it is dropped.

### Hub templates

Same as `rest` / `graphql` for streams with `replication_key`. Windowed
streams store a window position: compose the pipeline to a file
(`faucet hub compose ... --out composed.yaml`), point a copy's `state:` at a
scratch directory, run that copy once, and read the shape with
`faucet state show` before setting the real row.

## Many rows at once

Run `faucet state set --yes` once per row from a script, each preceded by its
`--dry-run`. `faucet state import` restores a document produced by
`faucet state export` (or moves state to another backend with `--to-state`);
it is the tool for backups and backend moves, not for hand-built bookmarks.

```bash
faucet state import pipeline.yaml state-before-migration.json --dry-run
faucet state import pipeline.yaml state-before-migration.json --overwrite --yes
```

The second line is the rollback for any carry-over mistake: it restores the
export exactly, deleting keys the export did not have.

## Airbyte state

Airbyte keeps per-stream state on the connection. The cursor value inside a
stream's state is what goes into `start_replication_value` /
`replication.initial_value` or `faucet state set`, as above. Airbyte state
blobs themselves are not a faucet format; never import them directly.
