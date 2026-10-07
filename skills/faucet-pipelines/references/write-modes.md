# Write modes

Write-mode fields sit directly in the sink's `config`. A sink supports a mode
only if its schema lists it: `faucet schema sink <type>` shows `write_mode`
and, for keyed modes, `key` and `delete_marker`.

| Mode | Meaning | Needs |
|---|---|---|
| `append` (default) | Insert every record. | Nothing. Duplicates on replay. |
| `upsert` | Insert or update by `key`. | Non-empty `key`. |
| `delete` | Delete by `key` for every record. | Non-empty `key`. |
| `overwrite` | Replace the whole destination with this run's records, swapped in only after the run succeeds. | No key. |

## Which sinks support what (this build)

- `upsert` / `delete` (keyed): `postgres`, `mysql`, `mssql`, `sqlite`,
  `mongodb`, `elasticsearch`, `bigquery`, `spanner`, `dynamodb`, `databricks`.
- `overwrite`: the above SQL/document sinks plus `file`, `s3`, `gcs`,
  `azure-blob`, `sftp`.
- Everything else appends. `iceberg` lists other modes in its schema but
  rejects anything except `append` when the sink is built; `snowflake`,
  `delta`, `kafka`, `redshift` and the queue sinks append.

## Upsert

```yaml
sink:
  type: postgres
  config:
    connection_url: "${env:PG_URL}"
    table_name: customers
    column_mapping: auto_map           # required: upsert cannot target a JSON blob column
    write_mode: upsert
    key: [id]                          # composite keys: [order_id, line_no]
    delete_marker: { field: __op, values: [d] }   # optional: matching rows become deletes
```

Rules:

- SQL sinks need column mode: `auto_map` (postgres, mysql, sqlite) or
  `auto_columns` (mssql). `faucet validate` does not catch upsert into the
  default JSONB-blob mode; it fails at run time.
- The destination needs a UNIQUE or PRIMARY KEY on exactly the `key` columns.
  A table the sink creates (`create_table: true`) gets one. A table you created
  must already have it; mysql checks that `key` matches a real unique index and
  fails fast if not.
- `bigquery` needs a defined table schema; `spanner` and `dynamodb` need `key`
  to equal the table's primary key.
- Within one batch, the last change per key wins.
- A record missing a key value, or with `null` in it, goes to the DLQ when one
  is configured; otherwise the batch fails.
- An upsert sink makes any source effectively-once: `faucet validate` reports
  `effectively-once (keyed upsert)`.

## Overwrite

```yaml
sink:
  type: sqlite
  config:
    database_url: "sqlite://./out/warehouse.db"
    table_name: contacts
    column_mapping: auto_map
    write_mode: overwrite
```

The run writes to a staging target and swaps it in after the run finishes
successfully; a failed or cancelled run leaves the old data untouched. Use it
for small reference tables that are re-read in full. Rejected at validate time
together with `delivery: exactly_once`, `schema.on_drift: evolve`,
`complete_for` cleanup, `shard:`, and a post-run `verify:` check. All rows that
write the same destination swap together, so a selection that leaves one of
them out is refused. `faucet run --limit N` writes to staging and discards it.

`postgres` and `bigquery` also support a scoped overwrite (`scope:` with a
`window`) that replaces only rows inside a date window; read
`faucet schema sink postgres` for the shape.

## Removing rows deleted at the source

Upsert never removes rows. Options, best first:

1. CDC source + `cdc_unwrap` + `delete_marker` (see `incremental-and-cdc.md`).
2. A soft-delete field mapped through `delete_marker: { field, values }`.
3. Scoped cleanup, when a fetch is complete for a scope (for example all
   children of one parent). `complete_for` goes on a source template (a
   matrix row's `source:` override does not accept it):

   ```yaml
   pipeline:
     sources:
       contacts_api:
         type: rest
         config: { base_url: "https://api.example.com", path: /contacts, records_path: "$.data[*]" }
       associations_api:
         type: rest
         config: { base_url: "https://api.example.com", path: "/contacts/${contacts.id}/associations", records_path: "$.data[*]" }
         complete_for:
           scope: { contact_id: "${contacts.id}" }   # destination column names
           on_missing: delete                        # default `ignore` deletes nothing
     sinks:
       warehouse:
         type: postgres
         config: { connection_url: "${env:PG_URL}", table_name: contacts, column_mapping: auto_map, write_mode: upsert, key: [id] }
   matrix:
     - id: contacts
       source: { ref: contacts_api }
       sink: { ref: warehouse }
     - id: associations
       parent: contacts
       source: { ref: associations_api }
       sink: { ref: warehouse, config: { table_name: contact_associations, key: [association_id] } }
   ```

   After the run, rows in the scope whose key was not written are deleted.
   Needs `write_mode: upsert` with a `key`; not allowed with
   `delivery: exactly_once`; skipped on failed, cancelled, `--dry-run` or
   `--limit` runs.
4. `write_mode: overwrite` for small tables re-read in full.

## Choosing

- Event or log data, never updated: `append`.
- Entity tables (customers, orders) from an incremental or windowed source: `upsert` with the natural key.
- CDC into a table: `upsert` + `delete_marker`.
- Small dimension re-read in full: `overwrite`.
- Anything that may be replayed (backfill, DLQ replay, at-least-once retry): prefer `upsert`.
