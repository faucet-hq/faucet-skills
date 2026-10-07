# Inventory the old setup

Write down every moving part before converting anything. The output of this
step is a table with one line per stream: source system, stream / table,
replication method and key, primary key, selected columns, mapper steps,
destination table, schedule, and where its current bookmark lives.

Read credentials by **name only**. Note which environment variable or secret
holds each value; never copy a value into notes, chat or a faucet config.

## Meltano: `meltano.yml`

A project can be split across files: follow every entry in `include_paths`
before concluding the inventory is complete.

| Block | What to record | Becomes in faucet |
|---|---|---|
| `plugins.extractors[]` | `name`, `variant`, `pip_url`, `inherit_from`, `config`, `select`, `metadata`, `schema` | a `pipeline.source` (or a hub template, or the `singer` source) |
| `plugins.loaders[]` | `name`, `variant`, `config` (target schema, batch size, `add_record_metadata`, load method) | a `pipeline.sink` |
| `plugins.mappers[]` | each `mappings[].name` and its `stream_maps` | `transforms:` / `masking:` ([selection-and-transforms.md](selection-and-transforms.md)) |
| `plugins.utilities[]`, transformers (dbt) | what runs after the load | stays where it is; faucet does EL only |
| `jobs[]` | `tasks` chains (`tap mapper target`, `dbt:run`) | one faucet config per extract-load; the rest stays in the orchestrator |
| `schedules[]` | `interval` (cron or `@hourly` / `@daily` ...), `job` or legacy `extractor`/`loader` | `schedule.cron` or an external scheduler ([schedules.md](schedules.md)) |
| `environments[]` | per-environment `config.plugins` overrides and `env` | `profiles:` overlays or separate files; check the prod values, not just the base |
| `default_environment` | which environment unqualified commands use | the profile you validate first |

`inherit_from` copies a parent plugin's settings; resolve the parent before
mapping the child.

### Extractor details

- **`select`** patterns are `<stream>.<property>`, with `*` wildcards and a
  leading `!` to exclude. `meltano select <extractor> --list --all` prints the
  resolved selection, which is easier to read than the patterns.
- **`metadata`** per stream holds `replication-method` (`FULL_TABLE`,
  `INCREMENTAL`, `LOG_BASED`) and `replication-key`. A stream with no entry
  uses the tap's default, often full table.
- **Stream ids** depend on the tap. Database taps usually name streams
  `<schema>-<table>` (`public-orders`); the bookmark is stored under that id.
- **`schema`** overrides change column types the target sees; note them,
  because faucet sinks infer types from records unless told otherwise.

### Where credentials and settings come from

Meltano layers settings, highest first: environment variables, the project
`.env`, the active environment's block in `meltano.yml`, the base plugin
block, then defaults. Sensitive settings set with `meltano config <plugin>
set` usually land in `.env`, not in `meltano.yml`.

The environment variable for a setting is `<PLUGIN_NAME>_<SETTING_NAME>`,
upper-cased with `-` turned into `_`: `tap-postgres` / `sqlalchemy_url` is
`TAP_POSTGRES_SQLALCHEMY_URL`; `target-postgres` / `password` is
`TARGET_POSTGRES_PASSWORD`. `meltano config <plugin> list` shows each
setting's source. In the faucet config, reference the **same variable**
(`${env:TAP_POSTGRES_SQLALCHEMY_URL}`) or a renamed one the user exports;
a Meltano SQLAlchemy URL may carry a driver suffix (`postgresql+psycopg2://`)
that a faucet `connection_url` does not accept, so a new variable holding a
plain `postgres://` URL is often needed.

### Bookmarks

`meltano state list` names every state id; for `meltano run` it is
`<environment>:<extractor>-to-<loader>` (plus a suffix when one is set).
`meltano state get <state_id>` prints `{"singer_state": {...}}`. The inner
`singer_state` object is what the tap receives as `--state`. Export each one to
a file now; [state-carryover.md](state-carryover.md) uses them. The state lives
in Meltano's system database or a `state_backend` (local file, S3, GCS, Azure)
configured in `meltano.yml`.

## Plain Singer (no Meltano)

| File | Holds |
|---|---|
| tap config (`config.json`) | connection settings and credentials |
| catalog (`catalog.json` / `properties.json`) | `streams[]` with `tap_stream_id`, `key_properties`, `schema`, and `metadata[]`; the entry with `breadcrumb: []` carries `selected`, `replication-method`, `replication-key`; `["properties", "<col>"]` entries carry per-column `selected` |
| state (`state.json`) | the last STATE value, usually `{"bookmarks": {"<stream>": {...}}}` |
| target config | destination settings |

Find the wrapper that runs `tap | target` (cron, a script, an orchestrator
task) to learn the schedule and which state file is passed with `--state`.

## Airbyte

Per connection, record: source and destination connector and settings, the
schedule (manual, interval or cron), namespace and stream-prefix settings, and
for every enabled stream its sync mode, cursor field and primary key. Read
these in the Airbyte UI or export them through its API or Terraform provider.

| Airbyte stream sync mode | faucet |
|---|---|
| Full refresh, Overwrite | full read, `write_mode: overwrite` |
| Full refresh, Append | full read, `write_mode: append` |
| Incremental, Append | bookmark on the cursor field, `write_mode: append` |
| Incremental, Append + Deduped | bookmark on the cursor field, `write_mode: upsert`, `key` = primary key |

The cursor field becomes the faucet bookmark field (`replication_key` on
`rest` / `graphql`, `replication.column` on SQL sources that have one). The
current cursor values are in the connection's state (in the UI under the
connection's advanced settings). Airbyte adds `_airbyte_*` columns to every
table; list which downstream models read them.

## Downstream contract

For every destination table, list what reads it (dbt models, dashboards,
exports) and which columns they depend on, including `_sdc_*` / `_airbyte_*`
metadata columns and table naming. A cutover that changes a table name,
column type or metadata column breaks those readers even when the rows match.
