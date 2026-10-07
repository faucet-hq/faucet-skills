# Incremental sync, CDC and mirrors

Every repeat-run pipeline should read only what changed. Pick the mechanism
the source connector actually has (check `faucet schema source <type>`), and
always attach a durable `state:` block so the bookmark survives between runs.

| Source | Mechanism | Keys |
|---|---|---|
| `rest`, `graphql` | Bookmark on a record field | `replication_method: { type: Incremental }`, `replication_key`, optional `start_replication_value`, `replication_bind`, `on_missing_key` |
| `mssql`, `redshift`, `clickhouse`, `spanner`, `databricks` | Bookmark on a column | `replication: { type: incremental, column, initial_value }` plus the placeholder in the query |
| `file` | New files since the last run | `incremental: { by: mtime }` or `{ by: name }` |
| `iceberg` | New snapshots | `mode` (see schema) |
| `kafka`, `kinesis` | Committed offsets / sequence numbers | `group_id` (kafka), `start_position` (kinesis) |
| `postgres-cdc`, `mysql-cdc`, `mongodb-cdc`, `mssql-cdc`, `dynamodb` streams | Log position | The CDC source config; position kept in state |
| `postgres`, `mysql`, `sqlite`, `duckdb`, `mongodb` (query) | None stored | Scope the query with `${now.*}`; replay with `faucet run --clock` or `faucet backfill` |

## REST / GraphQL bookmarks

```yaml
replication_method: { type: Incremental }
replication_key: updated_at          # field name, dot path (fields.updated) or JSON Pointer (/a.b/c)
start_replication_value: "2024-01-01T00:00:00Z"   # first-run lower bound
on_missing_key: keep                 # keep (default) | drop | fail
replication_bind:                    # push the bookmark to the server
  into: query                        # query | header | body | path
  name: updated_since                # or `path: /json/pointer` for into: body
  format: iso8601                    # raw | iso8601 | epoch_s | epoch_ms | date
  template: "${bookmark}"            # default; e.g. "updated_at gt ${bookmark}"
```

- Without `replication_bind` the filter is client-side only: the API still
  returns everything every run. Bind whenever the API accepts a filter.
- The key is read from the raw record, before transforms.
- If every record lacks the key, the bookmark does not advance and a warning
  fires; a misspelled key shows up as a stuck bookmark.
- Values compare by type: numbers numerically, decimal strings as decimals,
  RFC 3339 timestamps as instants, other strings lexicographically.
- For GraphQL, `replication_bind` names a GraphQL variable (`variable:`).
- `window:` on `rest` slices an incremental read into rolling `[start, end)`
  windows; read its fields from the schema.

## SQL query bookmarks

```yaml
query: "SELECT * FROM dbo.invoices WHERE modified_at > @bookmark ORDER BY modified_at"
replication:
  type: incremental
  column: modified_at
  initial_value: "2024-01-01T00:00:00"
```

The placeholder differs by connector: `@bookmark` for `mssql`, `clickhouse`,
`spanner`; `${bookmark}` for `redshift`, `databricks`. It is bound
injection-safe. Leave it out and the source still filters client-side but the
server scans the whole table each run. `state_key` sets an explicit bookmark
key; otherwise one is derived from the connection and query, so editing the
query text can start a fresh bookmark.

## Query sources with no bookmark

```yaml
query: >-
  SELECT * FROM public.orders
  WHERE updated_at >= '${now.date}'::date - 1 AND updated_at < '${now.date}'::date
```

Pair this with `write_mode: upsert` and a `key` so re-running a window
converges. `faucet run --clock 2026-06-02` replays a single window;
`faucet backfill` replays a range (see `scheduling-and-running.md`). For every
change including deletes, use the CDC source instead.

## CDC

CDC sources emit change envelopes (`op`, `before`, `after`, …), not rows. To
land rows in a table, add `cdc_unwrap` and an upsert sink with a delete marker:

```yaml
transforms:
  - type: cdc_unwrap                 # flat row + `__op` marker; drops ddl/truncate events
sink:
  type: postgres
  config:
    connection_url: "${env:DEST_PG_URL}"
    table_name: orders_mirror
    column_mapping: auto_map
    write_mode: upsert
    key: [id]
    delete_marker: { field: __op, values: [d] }
```

Source prerequisites:

- `postgres-cdc`: `wal_level = logical`; the publication must already exist
  (faucet does not create it); the slot is created when
  `create_slot_if_missing: true` (default). Keep `slot_type: permanent` (default).
  A slot retains WAL until faucet reads it, so a stopped pipeline grows WAL on
  the source.
- `mysql-cdc`: `binlog_format=ROW`, `binlog_row_image=FULL`,
  `binlog_row_metadata=FULL`, a unique `server_id`, replication grants.
- `mongodb-cdc`: a replica set or sharded cluster.
- `mssql-cdc`: CDC enabled on the table; list `capture_instances`.

Under `faucet run`, a streaming source stops on its termination knobs (for
`postgres-cdc`, `idle_timeout` seconds of silence, default 30; for `kafka`,
`idle_timeout` / `max_messages`, unset by default so set one). The next run
resumes from the stored position. Check each source's schema for its knobs.

## Mirror: snapshot, then CDC

A CDC stream starts at "now"; existing rows need a snapshot. `faucet mirror`
captures the log position first, snapshots the table, then streams from that
position, so nothing is missed or duplicated (with upsert).

```yaml
mirror:
  mode: snapshot_then_cdc            # required; the only mode
  continuous: true                   # keep streaming until SIGTERM (default true)
  snapshot:
    source:
      type: postgres                 # a non-CDC reader of the same database
      config:
        connection_url: "${env:SOURCE_PG_URL}"
        query: "SELECT * FROM public.orders"
```

Requirements, checked by `faucet validate`: `pipeline.source` is
`postgres-cdc`, `mysql-cdc` or `mongodb-cdc`; a durable `state:` (`memory` is
rejected); no `matrix:`; the sink should be `write_mode: upsert` with a `key`.
`faucet run` ignores the `mirror:` block; start it with `faucet mirror` and
watch it with `faucet mirror status`.

To mirror many tables over one change stream, add `mirror.tables` (`include` /
`exclude` globs, `new_tables: follow|ignore`, `without_primary_key:
refuse|append`, per-table overrides under `per_table`); `pipeline.sink` then
acts as a per-table template. Check the shape with `faucet schema mirror`.
