# Mapping taps and targets to faucet

Order of preference for each extractor or loader:

1. **A native faucet connector** (`faucet list`). Fastest, no Python.
2. **A Template Hub template** (`faucet hub list`) for a SaaS API. A native
   `rest` / `graphql` config someone already wrote and tested.
3. **The `singer` bridge** ([singer-bridge.md](singer-bridge.md)): the old tap
   or target, run unchanged. Experimental; keeps the Python runtime.

Only the rows below were checked against `faucet list` and the hub index for
v1.13.2. For anything else, run `faucet list`, `faucet search <keyword>` and
`faucet hub list` before deciding; never assume a connector exists. A
connector shown with `○` in `faucet list --available` is not in this build;
`faucet install <name> --kind source|sink` prints how to get it.

## Extractors → sources

| Tap (any variant) | faucet | Key differences |
|---|---|---|
| `tap-postgres` | `postgres` (query), `postgres-cdc`, or `faucet mirror` | The `postgres` source keeps **no bookmark** (faucet-hq/faucet-stream#825). `INCREMENTAL` → `${now.*}` window + upsert + `faucet backfill`; `LOG_BASED` → `postgres-cdc` (needs `wal_level=logical`, an existing publication) or `faucet mirror` for snapshot-then-CDC; `FULL_TABLE` → plain query + `write_mode: overwrite`. One matrix row per stream; `faucet discover` generates them. |
| `tap-mysql` | `mysql` (query) or `mysql-cdc` | Same as postgres: no stored bookmark on the query source. `LOG_BASED` → `mysql-cdc` (row-based binlog). |
| `tap-mssql` | `mssql` or `mssql-cdc` | `INCREMENTAL` → `replication: { type: incremental, column, initial_value }` with `@bookmark` in the query. |
| `tap-mongodb` | `mongodb` or `mongodb-cdc` | Query source has no bookmark; change streams via `mongodb-cdc`. |
| `tap-redshift` | `redshift` | `replication:` block; placeholder `${bookmark}`. |
| `tap-snowflake` | `snowflake` | No bookmark on the source; scope the query with `${now.*}`. |
| `tap-bigquery` | `bigquery` | No bookmark on the source; scope the query with `${now.*}`. |
| `tap-clickhouse` | `clickhouse` | `replication:` block; placeholder `@bookmark`. |
| `tap-csv`, `tap-spreadsheets-anywhere`, `tap-parquet` | `file` (local path, glob or http URL), `s3`, `gcs`, `azure-blob`, `sftp` | `file` reads CSV / JSON / JSONL / Excel / Parquet / Avro / ORC; `incremental: { by: mtime \| name }` replaces "only new files" logic. Other object-store sources read JSONL / JSON / text; check their schema. |
| `tap-rest-api-msdk` and other generic REST taps | `rest` | `api_url` + stream `path` → `base_url` + `path`; `records_path` stays JSONPath; `replication_key` → `replication_key` + `replication_method: { type: Incremental }`; `start_date` → `start_replication_value`; add `replication_bind` so the filter runs server-side. One config or matrix row per stream. |
| generic GraphQL taps | `graphql` | Same bookmark keys as `rest`; `replication_bind` names a GraphQL variable. |
| `tap-kafka` | `kafka` | Consumer group offsets replace the Singer bookmark; set `idle_timeout` / `max_messages` for batch runs. |
| `tap-dynamodb` | `dynamodb` | Scan, Query, or Streams (`mode`). |
| `tap-elasticsearch` | `elasticsearch` | Search / scroll; no bookmark. |

### SaaS taps → Template Hub templates

Each official template has a README in the hub
(`source-templates/faucet-hq/<name>.md`) with scopes, streams and which
streams are incremental. Compare its stream list with the tap's selected
streams before committing to it: `faucet hub rows faucet-hq/<name>`.

| Tap | Template id | Connector |
|---|---|---|
| `tap-github` | `faucet-hq/github` | `rest` |
| `tap-google-ads` | `faucet-hq/google-ads` | `rest` |
| `tap-google-analytics` (GA4 Data API) | `faucet-hq/google-analytics-4` | `rest` |
| `tap-hubspot` | `faucet-hq/hubspot` | `rest` |
| `tap-jira` | `faucet-hq/jira` | `rest` |
| `tap-facebook` (Marketing API) | `faucet-hq/meta-ads` | `rest` |
| `tap-salesforce` | `faucet-hq/salesforce` | `rest` (Bulk API 2.0) |
| `tap-shopify` | `faucet-hq/shopify` | `graphql` |
| `tap-stripe` | `faucet-hq/stripe` | `rest` |
| `tap-zendesk` | `faucet-hq/zendesk` | `rest` |

Differences that matter for a migration:

- **Stream names and shapes differ** from the tap's. Templates pin an API
  version and flatten or keep nested fields their own way, so downstream models
  usually need a rename layer or rework. Plan for it; do not promise
  drop-in tables.
- **Sync modes differ per stream.** A template may full-refresh a stream the
  tap synced incrementally (for example when the API cannot filter on
  updated time), and the reverse. Read the README's stream table.
- **Credentials are params**, passed with `--param name="$VAR"` or bound in an
  overlay, and the param names differ from the tap's setting names.
- **State keys** are `<template id>::<stream>` (for example
  `faucet-hq/github::issues`), so bookmarks survive a sink swap.

How to run, compose and pin templates is in the `faucet-templates` skill.

## Loaders → sinks

| Target (any variant) | faucet sink | Key differences |
|---|---|---|
| `target-postgres` | `postgres` (or hub `faucet-hq/postgres`) | Default mode is one `jsonb` column `data`; set `column_mapping: auto_map` for columns. `write_mode: upsert` + `key` replaces the target's key-property merge. `schema:` replaces `default_target_schema`. |
| `target-bigquery` | `bigquery` (or hub `faucet-hq/bigquery`) | `project_id`, `dataset_id`, `table_id`, `auth` required; `write_mode` append / upsert / delete / overwrite. |
| `target-snowflake` | `snowflake` | **Append only** in this release (no `write_mode` / `key`). A target that merged on key properties has no direct equivalent: land append-only and dedupe downstream, or pick another sink. |
| `target-redshift` | `redshift` | Append only; `write_strategy: copy` (S3 staging + `iam_role`) or `insert`. |
| `target-mysql` | `mysql` | append / upsert / delete / overwrite. |
| `target-mssql` | `mssql` | `write_mode` + `key`. |
| `target-duckdb` | `duckdb` | Append only. |
| `target-sqlite` | `sqlite` | `column_mapping` required; append / upsert / delete / overwrite. |
| `target-databricks` | `databricks` | append / upsert / delete / overwrite. |
| `target-jsonl`, `target-csv`, `target-parquet` | `file` | Format by extension (`.jsonl`, `.csv`, `.parquet`, ...); `write_mode: append \| overwrite`. |
| `target-s3` and similar | `s3` | `write_mode: append \| overwrite`. |
| any other target | `singer` sink | Runs the target unchanged: `target_command`, `target_config`, `env`. See [singer-bridge.md](singer-bridge.md). |

Always confirm a sink's keys with `faucet schema sink <type>` before writing it.

## Things with no faucet equivalent

Tell the user; do not paper over these.

- **dbt / transformers / utilities** in a job: faucet does EL only. Keep dbt
  and run it after `faucet run` in the orchestrator.
- **Mappers beyond record-level reshaping**: Python expressions in
  `stream_maps` that compute new values, stream aliasing that splits or
  merges streams, or `__filter__` expressions faucet's `filter` cannot express
  (its ops are `eq`, `ne`, `exists`, `in`, `not_in`). The `sql` transform
  that would cover many of these is not in the prebuilt binary
  (faucet-hq/faucet-stream#820); it needs a source build with that feature.
- **Multi-stream taps through the bridge**: the `singer` source emits one
  stream per config row.
- **Upsert into Snowflake, Redshift or DuckDB** in this release.
- **Meltano's state backends**: faucet uses its own `state:` store (`file`,
  `redis`, `postgres`).
