# Using hub templates

## Where templates come from

`--source` and `--sink` each take a **path** to a template file or a **hub id**.
An id resolves to `<hub>/source-templates/<owner>/<name>.yaml` or
`<hub>/sink-templates/<owner>/<name>.yaml`.

| Form | Means |
|---|---|
| `faucet-hq/github` | owner `faucet-hq`, name `github` |
| `github` | bare name: the official `faucet-hq/github`. If there is no official one, the CLI lists the community variants (`octo/erp (★ 37 · updated …)`) and asks you to pick. |
| `acme/erp@3` / `@stable` / `@newest` | a version selector (see [Pinning](#pinning-versions)) |
| `./my-template.yaml` | a local file, no catalog needed |

The hub is picked in this order: `--hub`, then `$FAUCET_HUB`, then `./hub` if
that directory exists, then the public `github:faucet-hq/template-hub`. A hub is
a directory, `github:owner/repo[@ref][/path]`, or a GitHub URL. A remote hub is
cached under `~/.cache/faucet/hub/` and reused offline with a warning.
`FAUCET_HUB_OFFLINE=1` skips the network. `GITHUB_TOKEN` (or
`FAUCET_GITHUB_TOKEN`) is sent when set. You need it for a private catalog, and
it raises the API rate limit.

Mix catalogs: a private source with the public sinks.

```bash
faucet run --source acme/erp --source-hub github:acme/private-hub \
  --sink faucet-hq/postgres --param pg_url="$PG_URL"
```

`--source-hub`, `--sink-hub` and `--overlay-hub` set the hub for one side.
Repeating `--hub` searches several hubs in order. A per-owner token goes in
`FAUCET_GITHUB_TOKEN_<OWNER>` (`acme-corp` becomes `FAUCET_GITHUB_TOKEN_ACME_CORP`).

## Discover

```bash
faucet hub list                         # sources and sinks: id, connector, streams or modes, stars, updated
faucet hub list --sort stars --json     # sort by stars or updated; --json for scripts
faucet hub matrix                       # source x sink grid: ✓ = every stream compatible, n/m = partial
faucet hub matrix --format markdown --out matrix.md
faucet hub rows faucet-hq/stripe        # one source's streams, their status and write preference
faucet hub rows faucet-hq/stripe --sink faucet-hq/postgres
```

Each official source template has a README beside it in the catalog
(`source-templates/faucet-hq/<name>.md`) listing required scopes, run times and
a changelog. Read it before you pick the credentials.

## Check and compose

```bash
faucet hub check --source faucet-hq/github --sink faucet-hq/jsonl
```

`hub check` prints the mode chosen for each stream. `overwrite→append` means the
sink satisfies the mode through an alias (the jsonl file is rewritten on every
run). It then prints a `faucet run` line with every required param. The exit
code is non-zero when any stream has no viable write mode. For example, an
`upsert`-only stream cannot run on `faucet-hq/jsonl`.

To see or keep the generated config:

```bash
faucet hub compose --source faucet-hq/github --sink faucet-hq/sqlite --out github-sqlite.yaml
faucet validate --source faucet-hq/github --sink faucet-hq/sqlite --show-composed
```

The composed document is an ordinary config. Its `name:` is the source id
(`faucet-hq/github`), so state keys are `faucet-hq/github::<stream>` and
bookmarks survive a sink swap. You can commit it or edit it like any config.

## Params and secrets

Params from both templates (and an overlay) merge into one set. If a name is
declared on both sides, the declarations must be identical.

```bash
faucet run --source faucet-hq/stripe --sink faucet-hq/bigquery \
  --param stripe_api_key="$STRIPE_API_KEY" \
  --param bq_project=analytics --param bq_sa_key="$BQ_SA_KEY"
```

- A param with a `default` needs no flag. A missing `required` param fails
  before anything runs, and the error names the param.
- `--param` values are coerced to the declared type, and a `values:` list is
  enforced at bind time.
- Param values are **literal**. `--param token='${env:TOKEN}'` is refused
  ("Param values are literal data"). Put the secret in an environment variable
  from your secret store and let the shell expand it.
- `secret: true` params are redacted from logs, errors and API responses as soon
  as they are bound.
- `--param-env NAME[=VALUE]` overrides an environment variable for `${env:NAME}`
  references in the config. It does not set a `${param.*}` value.
- `faucet validate` without `--param` binds required params to type-shaped
  placeholders, so CI can check structure without real credentials.

Before a real load, run against `faucet-hq/jsonl` or `faucet-hq/sqlite`. Both
need no infrastructure. sqlite has real overwrite and upsert semantics. It
writes `./out/faucet.db` by default (`sqlite_path`), and the directory must
exist.

## Deployment overlays (state, DLQ, alerts)

Neither template can name *your* state store or DLQ. A third document,
`kind: deployment`, adds operational blocks over the pairing:

```yaml
# ops/prod.yaml
kind: deployment
name: prod
description: Production state store for composed runs
params:
  state_dsn: { type: string, required: true, secret: true }
state: { type: postgres, config: { connection_url: "${param.state_dsn}" } }
```

```bash
faucet hub check --source faucet-hq/github --sink faucet-hq/bigquery --overlay ops/prod.yaml
faucet run --source faucet-hq/github --sink faucet-hq/bigquery --overlay ops/prod.yaml \
  --param state_dsn="$STATE_DSN" --param github_token="$GITHUB_TOKEN" \
  --param github_owner=octo-org --param github_repo=demo \
  --param bq_project=analytics --param bq_sa_key="$BQ_SA_KEY"
```

An overlay may set only `state`, `dlq`, `notifications` (alias `notify`), `sla`,
`profiling`, `policy`, `resilience`, `execution`, `delivery`, `schedule`, and
per-stream `sla` / `dlq` / `delivery` under `streams:`. It cannot change
connectors, streams or transforms. `--overlay` takes a file or an id under
`<hub>/deployments/`. Schema: `faucet schema deployment`.

Without a `state:` block, incremental streams re-read everything on every run.
`faucet validate` warns about this. A `memory` state store loses bookmarks when
the process exits.

## Running a subset of streams

`faucet run` takes the same selection flags as `faucet hub rows`:

```bash
faucet hub rows faucet-hq/github --select issues,pull_requests
faucet run --source faucet-hq/github --sink faucet-hq/sqlite --select issues \
  --param github_token="$GITHUB_TOKEN" --param github_owner=octo-org --param github_repo=demo
```

`--select` matches exact ids, `--only` matches globs, and `--skip` removes rows.

## Pinning versions

| Selector | Resolves to |
|---|---|
| none / `@stable` | the version the publisher marked stable (the default) |
| `@newest` | the highest version that is not deprecated, which may be a preview |
| `@3` | exactly version 3, the same body every time. If v3 is deprecated it still runs and prints a warning with the reason. |

```bash
faucet run --source faucet-hq/github@3 --sink faucet-hq/postgres \
  --param github_token="$GITHUB_TOKEN" --param github_owner=octo-org \
  --param github_repo=demo --param pg_url="$PG_URL"
```

Pin production jobs to `@N` and move the pin on purpose after reading the
template's changelog. Selectors need a catalog whose `index.json` records
versions, such as the public hub. A local directory hub returns
`this hub has no version history`, so drop the `@…` there.

## Registry and server use (`faucet template`)

`faucet template` is the parameterized-config **registry** that `faucet serve`
shares. It is separate from the hub catalog and has its own version numbers and
channels. Hub templates can be registered and triggered there by id:

```bash
faucet template register source-templates/faucet-hq/github.yaml --store sqlite:./templates.db --launch
faucet template register sink-templates/faucet-hq/postgres.yaml --store sqlite:./templates.db --launch
faucet template run faucet-hq/github --store sqlite:./templates.db --sink faucet-hq/postgres \
  --param github_token="$GITHUB_TOKEN" --param github_owner=octo-org \
  --param github_repo=demo --param pg_url="$PG_URL"
```

Here `--version` selects a registry version (`stable` by default, or `newest`
or a number). To mirror the whole hub into a server, use a sync file with a
`github` origin (`faucet serve --templates-sync ...`). A synced server skips a
template whose newest version is deprecated.
