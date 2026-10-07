# Sources and sinks

## Find out what this build has

```bash
faucet list                       # compiled-in sources, sinks, transforms, quality checks, state stores
faucet list --available           # the whole connector registry, including ones not compiled in
faucet search kafka               # registry search by name / keyword
faucet install <name>             # how to enable a connector (prints a recipe, runs nothing)
faucet conformance postgres       # maturity tier + capabilities of one connector
```

## Read the schema before writing a key

```bash
faucet schema source rest
faucet schema sink postgres
faucet schema transform cdc_unwrap
```

The output is JSON Schema: `required` lists mandatory keys, each property has a
`default` and a description, and enum-like values appear as `const` entries
under `oneOf`. Older docs and examples sometimes show stale shapes (for
example a postgres `column_mapping: { type: jsonb, column: data }`, where the
schema wants `{ jsonb: { column: data } }`); the schema wins.

Connector config keys that a connector does not declare are rejected with a
"did you mean" hint. Nested shapes (auth variants, pagination styles) are only
checked when the connector is built, which `faucet validate` does, so always
validate after editing.

## Picking a connector

- Database you control, periodic snapshot or `updated_at` window: the query
  source (`postgres`, `mysql`, `mssql`, `sqlite`, `duckdb`, `redshift`,
  `clickhouse`, `snowflake`, `bigquery`, `spanner`, `databricks`).
- Every change including deletes, low latency, no cursor column: the CDC
  source (`postgres-cdc`, `mysql-cdc`, `mongodb-cdc`, `mssql-cdc`, `dynamodb`
  in `mode: streams`).
- Files on disk or one HTTP(S) URL: `file` (format from the extension; JSONL,
  JSON, CSV, Excel, XML, Parquet, Avro, ORC). In a bucket: `s3`, `gcs`, `azure-blob`.
- HTTP APIs: `rest` (richest pagination, auth and incremental support),
  `graphql`, `xml` (XML/SOAP), `grpc`.
- Durable event streams: `kafka`, `kinesis`, `pubsub`, `rabbitmq`, `nats`,
  `sqs`, `redis`. Live push with no replay: `websocket`, `webhook`.
- `csv`, `parquet` and `jsonl` connectors are deprecated. Use `type: file`
  (local) or the object-store connector instead.

Queue sources that acknowledge what they read (`sqs`, `pubsub`, `rabbitmq`,
`nats` JetStream) are refused by `faucet preview`, `faucet run --dry-run`,
`faucet run --limit` and `faucet plan --live`, because a read would consume
the messages. Use `faucet plan --sample <fixture>` for those.

## Auth

Each connector defines its own auth shape. Read it from the schema; common ones
in this build:

| Connector | `auth` / credential variants |
|---|---|
| `rest` | `none`, `bearer`, `basic`, `api_key`, `api_key_query`, `oauth2`, `token_endpoint`, `custom`, or `{ ref: <name> }` |
| `bigquery` (source/sink) | `service_account_key_path` (`path`), `service_account_key` (`json`), `application_default` |
| `snowflake` (sink) | `key_pair` (`user`, `private_key_pem`), `oauth` (`token`), or `{ ref }` |
| `kafka` | `none`, `sasl_plain`, `sasl_scram` (`mechanism: sha256|sha512`), `ssl`, `sasl_ssl` |

Example:

```yaml
auth:
  type: bearer
  config:
    token: "${env:API_TOKEN}"
```

When several connectors share one credential, declare it once at top level and
reference it, so one token and one refresh are shared:

```yaml
auth:
  api:
    type: oauth2_refresh
    config:
      token_url: "${env:API_TOKEN_URL}"
      client_id: "${secret:API_CLIENT_ID}"
      client_secret: "${secret:API_CLIENT_SECRET}"
      refresh_token: "${secret:API_REFRESH_TOKEN}"
      persist: { path: ./state/auth }   # keep a rotated refresh token across runs
pipeline:
  source:
    type: rest
    config: { base_url: "https://api.example.com", auth: { ref: api } }
```

Top-level provider types: `static`, `oauth2`, `oauth2_refresh`,
`token_endpoint`, `google_service_account`, `flow`, `oauth1`.

## SQL sinks: blob vs columns

`postgres`, `mysql`, `sqlite`, `mssql` write either one JSON column (the
default for postgres, `{ jsonb: { column: data } }`) or one column per
top-level field (`auto_map`; `auto_columns` on mssql). Column mode is required
for upsert, delete and rollback, and is what schema-drift handling compares
against. With
`create_table: true` (default) the sink creates the table from the first page.

## REST essentials

```yaml
source:
  type: rest
  config:
    base_url: https://api.example.com/v1
    path: /items
    records_path: "$.data[*]"          # JSONPath to the record array
    pagination: { type: Cursor, next_token_path: "$.meta.next", param_name: cursor }
    max_pages: 1000                     # hard cap per run (default 100)
```

Pagination `type` values: `None`, `Cursor`, `CursorInBody`, `LinkHeader`,
`NextLinkInBody`, `PageNumber`, `Offset`, `OffsetInBody`, `RecordFieldCursor`.
Each style has its own fields; read them from `faucet schema source rest`.
`max_pages` (default 100) is a hard cap on pages per run; size it for the
largest expected sync, or set it to `null` to remove the cap. Rate-limit and retry knobs: `max_retries`, `retry_backoff`,
`retry_on_response`, `request_delay`, plus the top-level `resilience:` block.
