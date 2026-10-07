---
name: faucet-migrate
description: >-
  Use when migrating an existing Meltano project, Singer taps and targets, or
  Airbyte connections to faucet: reading and converting a meltano.yml
  (extractors, loaders, mappers, select and metadata, jobs, schedules,
  environments), choosing a native faucet connector, a Template Hub template or
  the Singer bridge for each tap and target, running an existing Singer tap or
  target inside faucet, moving incremental bookmarks and Singer state into
  faucet state so the first run does not re-sync, running old and new
  pipelines side by side, comparing them with `faucet verify`, and cutting over
  with a rollback path.
license: Apache-2.0
---

# Migrating to faucet from Meltano, Singer or Airbyte

A migration is four jobs: translate the configs, move the bookmarks, prove the
new pipeline produces the same data, and switch without losing the way back.
This skill covers the parts specific to coming from Meltano, Singer or
Airbyte. Writing the faucet configs themselves follows the `faucet-pipelines`
skill; operating and debugging them follows `faucet-debug`; hub templates
follow `faucet-templates`.

The binary is the source of truth. Check every connector with `faucet list`
and every key with `faucet schema` before writing it.

## Before you start

Check that `faucet` is on the `PATH` with `faucet --version`. If it is missing, install it with either:

```bash
# Homebrew (macOS / Linux)
brew install faucet-hq/faucet-stream/faucet-cli

# Installer script (macOS / Linux)
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/faucet-hq/faucet-stream/releases/latest/download/faucet-cli-installer.sh | sh
```

The prebuilt binary has no secrets-manager backends (`${aws-sm:…}`,
`${vault:…}`, …), no `sql` or `wasm` transforms, and no `catalog:` or
`notifications:` blocks (faucet-hq/faucet-stream#820). Default to
`${env:VAR}` for credentials. If the user needs one of those, it takes a
source build: `cargo install faucet-cli --features <feature>`.

## Hard rules

- **Never copy a credential value** out of `.env`, `meltano.yml`, a Singer
  config or Airbyte into a faucet config, a commit or the chat. Reference the
  environment variable that already holds it (`${env:TAP_POSTGRES_PASSWORD}`)
  or ask the user to export a new one.
- **Never delete or disable the old pipeline** (project, plugins, state,
  connections, tables, replication slots) until `faucet verify` passes and the
  user signs off. Disabling the old schedule happens at cutover, not before.
- **Export faucet state before every `faucet state set` or `state import`**
  (`faucet state export pipeline.yaml -o backup.json`), and run the mutating
  command with `--dry-run` first.
- **Never write to the old pipeline's tables during the parallel run.** faucet
  gets its own schema / dataset and its own state store.
- **Do not claim parity you have not verified.** If a mapper, sync mode or
  connector has no faucet equivalent, say so (see "Gaps" below).

## Workflow (follow in order)

1. **Inventory the old setup.** List every stream: source, replication method
   and key, primary key, selected columns, mapper steps, destination table,
   schedule, where its bookmark lives, and what reads the table downstream.
   Read credentials by name only. Follow `include_paths` and per-environment
   overrides in `meltano.yml`. See [references/inventory.md](references/inventory.md).
2. **Map each extractor and loader**, in this order of preference: a native
   connector (`faucet list`), then a Template Hub template (`faucet hub list`),
   then the Singer bridge. Record the key differences per stream.
   See [references/mapping.md](references/mapping.md) and
   [references/singer-bridge.md](references/singer-bridge.md).
   ```bash
   faucet list
   faucet search postgres
   faucet hub list
   faucet hub rows faucet-hq/github
   faucet schema source postgres
   ```
3. **Write the faucet configs** with the `faucet-pipelines` rules: `${env:…}`
   for secrets, a durable `state:` block, `write_mode: upsert` + `key` where the
   old target merged on key properties, one matrix row per stream. Translate
   `select`, stream maps and mappers per
   [references/selection-and-transforms.md](references/selection-and-transforms.md),
   and schedules / jobs per [references/schedules.md](references/schedules.md).
   For the parallel run, point every sink at a separate schema or dataset.
4. **Carry over state** for streams that will continue into existing tables,
   so the first run does not re-sync: Singer state into the bridge as-is,
   replication-key values into `start_replication_value` or `faucet state set`,
   and a backfill range for sources without a bookmark.
   See [references/state-carryover.md](references/state-carryover.md).
   ```bash
   faucet state export pipeline.yaml -o state-before-migration.json
   faucet state set pipeline.yaml --row row-0 --bookmark '"2026-10-06T23:58:12Z"' --dry-run
   faucet state show pipeline.yaml
   ```
5. **Validate, probe and test** before any real run.
   ```bash
   faucet validate --no-secrets pipeline.yaml
   faucet explain pipeline.yaml
   faucet doctor pipeline.yaml
   faucet preview pipeline.yaml --limit 10
   faucet run pipeline.yaml --limit 100
   ```
   A `--limit` run stores no bookmark. Add `faucet test` specs when the
   config has transforms or masking that replace a mapper.
6. **Run side by side and compare.** Run faucet on its schedule into its own
   dataset next to the old pipeline, then compare by content: faucet against
   the source, and faucet against the old pipeline's tables with a
   comparison-only config. Explain every difference before moving on.
   See [references/cutover.md](references/cutover.md).
   ```bash
   faucet verify pipeline.yaml --row orders
   faucet verify verify-old-vs-faucet.yaml --row orders --json
   ```
7. **Cut over and keep the way back.** Either repoint readers at faucet's
   dataset, or stop the old schedule, carry the final bookmark over and point
   faucet at the original tables. Export faucet state after the first
   production run, watch `faucet status`, and keep the old pipeline runnable
   (schedule disabled, nothing deleted) until the user signs off.

## Gaps to tell the user about

Check each against the user's inventory and say plainly which apply:

- **No stored bookmark on query sources.** The `postgres` and `mysql` sources
  (also `snowflake`, `bigquery`, `mongodb`, `sqlite`) keep no bookmark
  (faucet-hq/faucet-stream#825). A Meltano `INCREMENTAL` `tap-postgres` stream
  becomes a `${now.*}` window with upsert plus `faucet backfill` for history,
  or `postgres-cdc` / `faucet mirror` when deletes or exact change capture
  matter. There is no bookmark to carry over; the old bookmark sets the
  backfill start.
- **The Singer bridge is experimental**, single-stream per row, needs the
  tap's Python runtime on every host, runs at tap speed, and resumes at the
  tap's STATE granularity, so it needs a keyed (upsert) sink to avoid
  duplicates.
- **Mappers without an equivalent**: Python expressions, date math,
  cross-field arithmetic and stream splitting beyond `filter`. The `sql`
  transform is not in the prebuilt binary. md5 hashing is not available
  (`hash` is SHA-256 or BLAKE3), so hashed join keys change.
- **Append-only sinks**: `snowflake`, `redshift` and `duckdb` sinks have no
  upsert in this release; a merging target there has no direct equivalent.
- **Template streams are not the tap's streams.** Names, shapes and sync modes
  differ; downstream models need adjusting.
- **Metadata columns differ.** `_sdc_*` / `_airbyte_*` columns are not
  reproduced one-for-one; `metadata_columns:` with `prefix: _sdc` gives
  `_sdc_extracted_at` and `_sdc_loaded_at` only.
- **Plugins with no data-movement role** (dbt, utilities, orchestrators) stay
  outside faucet.

## Task → reference

| Task | Read |
|---|---|
| Reading `meltano.yml`, env var names, Singer catalog / state files, Airbyte connections | [references/inventory.md](references/inventory.md) |
| Which faucet source / sink / template replaces a tap or target | [references/mapping.md](references/mapping.md) |
| Running an unmapped tap or keeping a target through the `singer` connectors | [references/singer-bridge.md](references/singer-bridge.md) |
| Moving bookmarks: `state set`, `state import`, `start_replication_value`, backfill | [references/state-carryover.md](references/state-carryover.md) |
| `select`, stream maps, mappers, `_sdc_*` columns | [references/selection-and-transforms.md](references/selection-and-transforms.md) |
| Schedules, jobs, orchestrators | [references/schedules.md](references/schedules.md) |
| Parallel run, `faucet verify`, cutover options, rollback | [references/cutover.md](references/cutover.md) |

## Worked example

[examples/meltano.yml](examples/meltano.yml) is a Meltano project with a
`tap-postgres` (incremental, with a hashing mapper), a generic REST tap and
`target-postgres`, run by two jobs on two schedules. It converts to:

- [examples/app-db-to-postgres.yaml](examples/app-db-to-postgres.yaml): matrix
  of tables, `${now.*}` daily window, upsert, `hash` + `drop` in place of the
  mapper, `_sdc` metadata columns, backfill defaults, cron schedule.
- [examples/tickets-api-to-postgres.yaml](examples/tickets-api-to-postgres.yaml):
  `rest` incremental with the Meltano bookmark as `start_replication_value`,
  server-side filter, hourly schedule.
- [examples/verify-meltano-vs-faucet.yaml](examples/verify-meltano-vs-faucet.yaml):
  comparison-only config for `faucet verify` during the parallel run.
- [examples/singer-bridge-to-postgres.yaml](examples/singer-bridge-to-postgres.yaml):
  a tap with no native equivalent, run through the `singer` source into an
  upsert sink.

All pass `faucet validate --no-secrets --no-env-file` with placeholder values
exported for their `${env:…}` variables.
