# Many datasets: matrix, templates, discovery

## Templates + matrix rows

Declare connections once as named templates, then one `matrix` row per
dataset. Each row deep-merges onto the pipeline (objects merge, arrays and
scalars replace) and gets its own state key `{name}::{row_id}`.

```yaml
pipeline:
  sources:
    app_db:
      type: postgres
      config: { connection_url: "${env:APP_DB_URL}", query: "SELECT 1" }
  sinks:
    warehouse:
      type: bigquery
      config:
        project_id: acme-analytics
        dataset_id: app_raw
        table_id: placeholder
        auth: { type: application_default }
        write_mode: upsert
  state: { type: file, config: { path: ./.faucet-state } }

matrix:
  - id: customers
    source: { ref: app_db, config: { query: "SELECT * FROM public.customers" } }
    sink:   { ref: warehouse, config: { table_id: customers, key: [id] } }
  - id: orders
    depends_on: [customers]                 # start only after customers succeeds
    source: { ref: app_db, config: { query: "SELECT * FROM public.orders" } }
    sink:   { ref: warehouse, config: { table_id: orders, key: [id] } }
```

- A row without `ref:` uses the template named `default` (`faucet init`
  writes `pipeline.sources.default` / `pipeline.sinks.default`).
- Row ids are stable identifiers: renaming one starts a fresh bookmark.
  `now` and `tenant` are reserved.
- Give each row its own destination (`table_id`, `table_name`, `path`).
  `faucet run` refuses rows that share one fixed output file, and shared
  tables mix datasets.
- A row's `source:` override accepts `ref`, `type`, `config`, `status`,
  `attributes`. Template-only keys (`complete_for`, `transforms`,
  `inherit_transforms`, `tags`) go on the template under `pipeline.sources`;
  row `transforms` and `tags` go on the row itself.
- `execution: { max_concurrent: N, on_error: continue|stop }` bounds parallel
  rows. `execution.schedule: lpt` starts the heaviest rows (`weight`) first.

## Parent / child rows (per-record fan-out)

```yaml
matrix:
  - id: users
    source: { ref: api, config: { path: /users } }
  - id: posts
    parent: users                            # runs once per users record
    source: { ref: api, config: { path: "/users/${users.id}/posts" } }
```

`${<parent_id>.<field>}` resolves per parent record (bound as a parameter in
SQL queries). Children get state keys per parent record.

Other fan-out forms (check with `faucet schema config`): `fan_out:` +
`for_each:` rows enumerate values from a live endpoint and run once per
combination; top-level or per-row `partition:` splits one row into chunked
range invocations with `${partition.*}` tokens (`bounds` is required for
`kind: integer`).

## Selecting rows at run time

```bash
faucet run pipeline.yaml --select customers            # by id (repeatable)
faucet run pipeline.yaml --only 'stg_*'                # by glob
faucet run pipeline.yaml --skip orders
faucet run pipeline.yaml --tag finance                 # rows carrying the tag
faucet run pipeline.yaml --status available            # include parked rows
faucet validate pipeline.yaml --select orders          # check a selection without running
```

A selected row whose `depends_on` / `parent` ancestor is not selected is an
error under the default `include_parents: off`; select the ancestor too, or
pass `--include-parents eligible|all`. Rows can carry `tags: [..]`, and a
source can carry `status: mandatory|active|available|draft|archived`
(default `active`; `available` and below run only with `--status`).

## Generating rows: `faucet discover`

Write a connection-only config, then let discovery enumerate datasets:

```yaml
# conn.yaml
version: 1
name: warehouse_sync
pipeline:
  source:
    type: postgres
    config:
      connection_url: "${env:DATABASE_URL}"
      query: SELECT 1            # placeholder; discovery ignores it
  sink:
    type: file
    config: { path: ./out.jsonl }
  state: { type: file, config: { path: ./.faucet-state } }
```

```bash
faucet discover conn.yaml --include 'public.*' --exclude '*.tmp_*' -o pipeline.yaml
faucet discover conn.yaml --json            # dataset list only
faucet validate --no-secrets pipeline.yaml  # generated configs validate
```

Output is your config with a generated `matrix:` (one row per table,
collection, index or prefix, with column types and row estimates as comments
and a `weight` per row). Secrets stay as references. A fixed file sink path
gets the row id inserted (`./out.jsonl` → `./out.public_orders.jsonl`).
Supported sources: `postgres`, `mysql`, `mssql`, `sqlite`, `mongodb`,
`elasticsearch`, `bigquery`, `snowflake`, `spanner`, `s3`, `gcs`, `file`,
`iceberg`, `dynamodb`, and `rest` with an `odata:` or `discovery:` block.

After generating: review every row, switch tables with a natural key to
`write_mode: upsert` + `key`, add incremental scoping where the source supports
it, and re-validate. To mirror a whole database continuously, use
`mirror.tables` instead (see `incremental-and-cdc.md`).
