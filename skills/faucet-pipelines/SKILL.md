---
name: faucet-pipelines
description: >-
  Use when writing, editing, reviewing or debugging a faucet pipeline config
  (YAML/JSON run by the `faucet` CLI): moving data from one system to another
  (database, API, files, object storage, queue, warehouse), setting up an
  incremental sync, a CDC stream or a snapshot-then-CDC mirror, choosing write
  modes (append, upsert, delete, overwrite) and keys, adding transforms, PII
  masking, data-quality checks, data contracts, schema-drift handling or a
  data-flow policy, configuring state, dead-letter queues or exactly-once
  delivery, scheduling or backfilling a pipeline, generating per-table configs
  with discovery, and wiring credentials through secret references.
license: Apache-2.0
---

# faucet pipelines

`faucet` runs data pipelines declared in a YAML (or JSON) file: one source,
optional transforms and governance passes, one sink, plus optional state,
DLQ, schedule and runtime blocks. This skill is how to write those files so
they are correct the first time and stay correct when they run unattended.

The binary is the source of truth. Connector config keys, enum values and
defaults come from `faucet schema`, not from memory or from older examples.
Unknown keys are rejected, so a guessed key fails loudly at `faucet validate`.

## Before you start

Check that `faucet` is on the `PATH` with `faucet --version`. If it is missing, install it with either:

```bash
# Homebrew (macOS / Linux)
brew install faucet-hq/faucet-stream/faucet-cli

# Installer script (macOS / Linux)
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/faucet-hq/faucet-stream/releases/latest/download/faucet-cli-installer.sh | sh
```

## Workflow (follow in order)

1. **Pick connectors and read their schemas.**
   ```bash
   faucet list                      # compiled-in sources, sinks, transforms, checks
   faucet schema source postgres    # every key, type, default, required field
   faucet schema sink bigquery
   faucet schema transform cast     # same for a transform's config
   ```
   A connector missing from `faucet list` is not in this build. Never invent
   a key; if the schema does not have it, the connector does not support it.
2. **Write the YAML.** Start from `faucet init -o pipeline.yaml --source <type> --sink <type>`
   (a commented skeleton with every field) or from a file in `examples/`.
   For a whole database, generate rows with `faucet discover conn.yaml -o pipeline.yaml`.
3. **Validate until clean.**
   ```bash
   faucet validate --no-secrets pipeline.yaml
   ```
   `--no-secrets` skips secrets-manager lookups (`${vault:…}`, `${aws-sm:…}`,
   `${gcp-sm:…}`, `${azure-kv:…}`). `${env:VAR}` / `${secret:VAR}` are still
   resolved, so export them (dummy values are fine) or put them in a sibling
   `.env`. Each row line ends with the derived delivery guarantee; read it.
4. **Confirm intent.**
   ```bash
   faucet explain pipeline.yaml                         # plain-English narration, offline
   faucet plan pipeline.yaml --sample fixtures.jsonl    # resolved chain, output schema, sink delta; zero writes
   ```
5. **Probe real systems** once credentials exist: `faucet doctor pipeline.yaml`
   (auth, network, permissions; non-zero exit on any failure). `faucet doctor
   --offline` gives credential-free lints.
6. **Test logic offline** when the config has transforms, quality checks,
   masking or a contract: write a spec and run `faucet test tests/*.yaml`.
7. **Run.** `faucet run pipeline.yaml` (try `--limit 100` or `--dry-run` first),
   `faucet schedule pipeline.yaml` for cron, `faucet mirror` for snapshot-then-CDC,
   `faucet backfill` for a historical range.

## Hard rules

- **Never inline secrets.** Every password, token, key and credentialed URL
  is a reference: `${env:VAR}`, `${secret:VAR}`, `${file:PATH}`, `${vault:path#field}`,
  `${aws-sm:name#field}`, `${gcp-sm:projects/p/secrets/s/versions/latest}`,
  `${azure-kv:vault/secret}`. The prebuilt binary (Homebrew / installer script)
  has no secrets-manager backends, so default to `${env:VAR}` unless the user's
  build includes them (`cargo install faucet-cli --features secrets`). Do not run
  with `FAUCET_LOG=debug` when configs hold secrets. See `references/secrets.md`.
- **Never invent config keys.** Read `faucet schema` for the exact type. The
  top-level grammar is in `faucet schema config`.
- **Prefer incremental over full re-reads.** Use the connector's bookmark mode
  where it has one: `replication_method` + `replication_key` (`rest`,
  `graphql`), a `replication:` block (`postgres` and `mysql` from faucet-cli
  1.14, `mssql`, `redshift`, `clickhouse`, `spanner`, `databricks`),
  `incremental:` (`file`), or a CDC source. Query sources without one (e.g.
  `sqlite`, `duckdb`, and `postgres`/`mysql` before 1.14) are scoped with
  `${now.*}` windows plus `faucet backfill`, which misses rows when a run is
  skipped. See `references/incremental-and-cdc.md`.
- **Incremental needs a durable `state:` block** (`file`, `redis` or
  `postgres`; `memory` forgets everything at exit). Without state there is no
  resume and every run starts over.
- **Upsert and delete need `key`.** `write_mode: upsert|delete` with an empty
  `key` is rejected. SQL sinks also need column mode (`column_mapping: auto_map`
  for postgres/mysql/sqlite, `auto_columns` for mssql); validate does not catch
  upsert into the default JSONB-blob mode. See `references/write-modes.md`.
- **Quarantine needs a DLQ.** Any `on_failure: quarantine`, contract
  `on_breach: quarantine`, or drift `quarantine` requires a `dlq:` block. A DLQ
  always appends and holds the only copy of those rows; inspect and replay with
  `faucet dlq`. The atomic-watermark exactly-once path forbids a DLQ.
- **Do not claim exactly-once without validate saying so.** The row line must
  read `effectively-once (atomic watermark)` or `effectively-once (keyed upsert)`.
- **Bind listeners to localhost.** `observability.prometheus.listen: "127.0.0.1:<port>"`
  and `faucet serve --listen 127.0.0.1:<port>` (the default) unless the user
  asks for wider exposure and puts auth in front of it. Never use `--no-auth`
  outside a throwaway local test.
- **Re-validate after every edit.** A config that has not passed
  `faucet validate` is not done.

## Config anatomy

Validated skeleton (every block below `pipeline.source`/`sink` is optional):

```yaml
version: 1                      # required, always 1
name: orders_sync               # used in state keys, metrics, lineage
vars: { region: eu }            # reuse as ${vars.region}
delivery: at_least_once         # or exactly_once (validate checks the requirements)
schedule: { cron: "0 2 * * *", timezone: UTC }        # read only by `faucet schedule`
resilience: { retry: { max_attempts: 5 } }
sla: { min_rows_per_run: 1 }
budget: { max_records: 10000000 }
lineage: { namespace: prod, transport: { type: file, config: { path: ./lineage.jsonl } } }
observability: { prometheus: { listen: "127.0.0.1:9464" } }
policy:
  classifications: [{ label: pii, fields: [email] }]
  rules: [{ name: pii-masked, when: { label: pii }, mask: [hash] }]
pipeline:
  source: { type: file, config: { path: ./in/orders.csv } }
  transforms: [{ type: keys_case, config: { mode: snake } }]
  masking: { rules: [{ match: { fields: [email] }, action: { type: hash } }] }
  quality: { record: [{ type: not_null, field: id, on_failure: quarantine }] }
  contract: { version: "1.0.0", fields: [{ name: id, type: string }] }
  schema: { on_drift: warn }
  dlq: { sink: { type: file, config: { path: ./dlq/orders.jsonl } } }
  sink: { type: file, config: { path: ./out/orders.jsonl } }
  state: { type: file, config: { path: ./.faucet-state } }
execution: { max_concurrent: 4, on_error: continue }
```

Per-page order: source → transforms → masking → quality → contract → schema
drift → sink; the bookmark advances only after the sink confirms the page.
Many-table configs replace the single `source`/`sink` with named templates
(`pipeline.sources`, `pipeline.sinks`) and a `matrix:` of rows that `ref:`
them. Other top-level blocks (`params`, `auth`, `partition`, `mirror`,
`backfill`, `verify`, `rollback`, `profiling`, `reconcile`, `usage`,
`metadata_columns`, `selection`) are covered in the references. Details:
`references/config-anatomy.md`.

## Task → reference

| Task | Read |
|---|---|
| Top-level grammar, interpolation, composition (`extends`, `profiles`), `params` | `references/config-anatomy.md` |
| Choosing connectors, reading schemas, auth blocks, deprecated connectors | `references/sources-and-sinks.md` |
| Incremental sync, bookmarks, CDC, snapshot-then-CDC mirror | `references/incremental-and-cdc.md` |
| append / upsert / delete / overwrite, keys, delete markers, scoped cleanup | `references/write-modes.md` |
| Reshaping records, transform order, `cdc_unwrap`, `faucet test` specs | `references/transforms.md` |
| Masking, quality checks, contracts, schema drift, data-flow policy | `references/governance.md` |
| State stores, DLQ, exactly-once, resume, `faucet state` / `faucet status` | `references/state-and-delivery.md` |
| Secret references and how validate treats them | `references/secrets.md` |
| `run` / `schedule` / `backfill` / `serve` flags, SLA, budgets, metrics | `references/scheduling-and-running.md` |
| Many tables: `matrix`, templates, `depends_on`, row selection, `faucet discover` | `references/matrix-and-discover.md` |

## Worked examples

All pass `faucet validate --no-secrets` as shipped:

- `examples/rest-incremental-to-postgres-upsert.yaml`: REST incremental with
  server-side bookmark, transforms, quality + DLQ, keyed upsert, hourly schedule.
- `examples/postgres-cdc-mirror.yaml`: `faucet mirror` snapshot-then-CDC,
  `cdc_unwrap`, delete marker, schema `evolve`, exactly-once, postgres state.
- `examples/files-to-s3-parquet-masked.yaml`: new-files-only CSV ingest,
  masking, contract quarantine, dated Parquet prefix on S3.
- `examples/kafka-to-postgres-exactly-once.yaml`: atomic-watermark
  exactly-once from Kafka, Prometheus on localhost.
- `examples/postgres-tables-to-bigquery-daily.yaml`: matrix of tables with
  shared templates, `depends_on`, `${now.*}` windows, backfill defaults, SLA.
- `examples/mssql-incremental-to-snowflake.yaml`: SQL `replication:` bookmark
  with `@bookmark` pushdown.

## When validate fails

- `unknown field X, expected one of …`: a structural typo; use one of the listed names.
- `unknown <kind> config key(s) … did you mean …`: a connector key typo; check `faucet schema`.
- `missing field X`: a required connector field; the schema's `required` list names it.
- `missing environment variable 'X'`: export it or add it to `.env`, even with `--no-secrets`.
- `built without the … feature`: the binary lacks that connector, secrets backend or
  block. Use a build that has it; do not rewrite the config around the gap.
