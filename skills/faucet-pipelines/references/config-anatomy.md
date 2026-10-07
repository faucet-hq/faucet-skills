# Config anatomy

`faucet schema config` prints the JSON Schema for the whole document. Add
`# yaml-language-server: $schema=<path-to-saved-schema>.json` to the top of a
config to get editor completion.

## Top-level keys

| Key | Purpose |
|---|---|
| `version` | Required. Always `1`. |
| `name` | Pipeline name. Part of every state key (`{name}::{row_id}`); renaming it orphans existing bookmarks. |
| `vars` | Reusable values, referenced as `${vars.KEY}`. |
| `params` | Typed run-time parameters, referenced as `${param.NAME}`, bound with `--param NAME=VALUE`. |
| `auth` | Named shared auth providers; connectors reference one with `auth: { ref: <name> }`. |
| `pipeline` | Required. Source, transforms, governance blocks, DLQ, sink, state. |
| `matrix` | Rows that each run the pipeline with overrides (many tables, fan-out). See `matrix-and-discover.md`. |
| `execution` | `max_concurrent`, `on_error: continue|stop`, `schedule: declared|lpt`. |
| `selection` | `include_parents: off|eligible|all` for row selection. |
| `delivery` | `at_least_once` (default) or `exactly_once`. Overridable per matrix row. |
| `schedule` | Cron block, read only by `faucet schedule`. |
| `mirror` | Snapshot-then-CDC block, read only by `faucet mirror`. |
| `backfill` | Defaults for `faucet backfill`. |
| `partition` | Split a row into chunked range invocations (`${partition.*}` tokens). |
| `resilience` | Retry, circuit breaker, poison-row handling. |
| `sla`, `profiling`, `reconcile`, `verify` | Post-run checks: freshness/volume, column drift, row-count reconciliation, content verification. |
| `rollback` | Make runs undoable (`faucet rollback`). |
| `budget`, `usage` | Run ceilings; cost estimates. |
| `policy` | Data-flow policy (which labelled columns may reach which sinks). |
| `lineage` | OpenLineage emission. |
| `metadata_columns` | Stamp `_faucet_*` columns on every row. |
| `observability` | `prometheus` (`listen`, `buckets`), `otel`, `tracing`. |

Some blocks are build features. If validate says `unknown field` for a key that
the docs describe (for example `notifications` or `catalog`), the binary was
built without that feature; `faucet schema config` lists what this build accepts.

## `pipeline`

| Key | Purpose |
|---|---|
| `source` / `sink` | `{ type, config }`. `config` keys come from `faucet schema source|sink <type>`. |
| `sources` / `sinks` | Named templates for matrix rows (`ref: <name>`). `faucet init` writes these as `default`. |
| `transforms` | Ordered list of `{ type, config }`. |
| `masking`, `quality`, `contract`, `schema` | Governance passes. See `governance.md`. |
| `dlq` | Dead-letter queue. See `state-and-delivery.md`. |
| `state` | `{ type: memory|file|redis|postgres, config }`. |
| `nodes` / `edges` | Topology mode (fan-out / fan-in / join graphs). Mutually exclusive with `matrix`. |

A sink may carry `attributes:` (free-form strings such as `residency: eu`) for
policy rules. Sinks reject `transforms:`; put shaping on the pipeline or row.

## Interpolation

Resolved when the file loads:

- `${env:VAR}`, `${secret:VAR}` (alias of env), `${file:PATH}`.
- `${vars.KEY}`, `${sources.NAME.PATH}`, `${sinks.NAME.PATH}`.
- Secrets managers last: `${vault:…}`, `${aws-sm:…}`, `${gcp-sm:…}`, `${azure-kv:…}`.

Resolved later:

- `${param.NAME}` at trigger time.
- `${now.date}`, `${now.datetime}`, `${now.year}`, `${now.month}`, `${now.day}`,
  `${now.hour}`, `${now.unix}`, `${now.strftime.<fmt>}` at run time. Allowed in
  source and sink config values and in a `set` transform's values; rejected in
  `state:`, `dlq:` and other transform configs. Clock: process start (UTC) for
  `faucet run` (override with `--clock`), the tick time in the schedule's
  timezone for `faucet schedule`.
- `${<row_id>.<path>}` per parent record in matrix DAG rows. In SQL source
  queries these tokens are bound as parameters, not spliced into the SQL.

An unknown `${now.*}` token is an error. A matrix row cannot be named `now` or `tenant`.

## Composition

| Mechanism | Form | Effect |
|---|---|---|
| `extends` | `extends: ./base.yaml` (or a list) | Deep-merge the child over the base(s). |
| `profiles` | `profiles: { dev: {…}, prod: {…} }` | Overlay selected with `--profile NAME` or `FAUCET_PROFILE`. |
| `!include` | `key: !include ./frag.yaml` | Substitute a YAML fragment (YAML only). |

Merge rule: objects merge, arrays replace, scalars replace. Precedence, lowest
first: base, child, profile, matrix row. Check the merged result with
`faucet validate --show-composed pipeline.yaml --profile prod`. Composition
applies to files read from disk, not to configs submitted to `faucet serve`.

## `params`

```yaml
params:
  tenant_id: { type: string, required: true, description: "Tenant to sync" }
  since:     { default: "1970-01-01" }
  page_size: { type: int, default: 500 }
  api_token: { required: true, secret: true }
  region:    { default: us, values: [us, eu, apac] }
```

`type` is `string|int|float|bool`. `required` and `default` are exclusive.
`secret: true` registers the value for redaction. `faucet validate` with no
`--param` binds required params to placeholders; pass `--param` to check a
concrete invocation.

## Housekeeping commands

```bash
faucet fmt pipeline.yaml --check      # canonical key order (comments are not preserved when rewriting)
faucet migrate pipeline.yaml --check  # upgrade an older grammar
faucet explain pipeline.yaml --rows   # narration per expanded row
```
