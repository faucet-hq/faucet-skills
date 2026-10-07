# Sink-template anatomy

Schema: `faucet schema sink-template`. The file lives at
`sink-templates/<owner>/<name>.yaml`. The hub's official sinks are
`faucet-hq/bigquery`, `faucet-hq/postgres`, `faucet-hq/sqlite` and
`faucet-hq/jsonl`. Copy the closest one.

```yaml
kind: sink-template
name: postgres
owner: faucet-hq
description: PostgreSQL — one auto-mapped table per stream, transactional overwrite or ON CONFLICT upsert
tags: [database]
params:
  pg_url:    { type: string, required: true, secret: true, description: "postgres://user:pass@host:5432/db connection URL" }
  pg_schema: { type: string, default: public, description: "Schema every stream's table is created in" }
sink:
  type: postgres
  config:
    connection_url: "${param.pg_url}"
    schema: "${param.pg_schema}"
    column_mapping: auto_map
per_stream:
  table_name: "${stream}"
```

## Fields

| Field | Notes |
|---|---|
| `kind`, `name`, `owner`, `description`, `tags`, `docs` | As for a source template. `name` equals the file stem. |
| `params` | Destination params. They merge with the source's params, and a name declared on both sides must be declared identically. Prefix them (`pg_`, `bq_`) to avoid collisions. Credentials (connection URLs with passwords, key JSON) are `secret: true` with no default. |
| `sink` | `{ type, config }`. `faucet schema sink <type>` lists the config fields. |
| `per_stream` | The addressing rule. Each key is copied into every stream's sink config with substitutions. |
| `write_mode_aliases` | Declares a mode the destination satisfies by construction. |

## `per_stream` substitutions

| Token | Value |
|---|---|
| `${stream}` | The stream name. Use it for the table name. |
| `${source}` | The source template's short `name` (no owner, because table names cannot contain `/`). |
| `${owner}` | The source template's owner, for file paths. |
| `${param.*}` | Any merged param. |

```yaml
per_stream:
  table_id: "${stream}"                                   # warehouse
# per_stream:
#   path: "${param.out_dir}/${owner}/${source}/${stream}.jsonl"   # files
```

## Never set `write_mode` or `key`

The composer injects `write_mode` (the first mode in the stream's `write` list
that this sink supports) and `key` (the stream's `primary_keys`) into each
stream's sink config. The supported modes come from the connector registry.
`faucet hub list` shows them per sink (`append|upsert|delete|overwrite`).

## `write_mode_aliases`

A sink that rewrites its output on every run *is* a full refresh, even if its
connector only knows `append`. The hub's jsonl sink declares this:

```yaml
sink:
  type: jsonl
  config:
    append: false              # file is rewritten every run
per_stream:
  path: "${param.out_dir}/${source}/${stream}.jsonl"
write_mode_aliases:
  overwrite: append            # a stream that wants overwrite runs as append here
```

Rules the composer enforces:

- The target mode must be one the connector supports.
- Aliasing a mode the connector already supports natively is refused as
  redundant.
- Keyed modes (`upsert`, `delete`) cannot be aliased. Only a sink that
  deduplicates by key can honour them.
- A child stream (`parent:`) cannot satisfy `overwrite` through an alias, and
  cannot run on a sink that truncates on every invocation. It falls through to
  the next mode in its `write` list or fails the pairing, and the error names
  the stream.

## Check a new sink template

```bash
faucet hub lint --hub .
faucet hub check --hub . --source faucet-hq/example-rest-api --sink <your-login>/<name>
faucet hub check --hub . --source faucet-hq/github --sink <your-login>/<name>
faucet hub matrix --hub .
```

The hub's PR checklist checks a sink template against
`faucet-hq/example-rest-api`. Running `faucet hub matrix` shows which official
sources compose with it in full (`✓`) or in part (`n/m`).
