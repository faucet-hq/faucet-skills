# Testing a template

Everything below runs offline and needs no credentials. Run it from the root of
a hub checkout (or any directory laid out like one: `source-templates/`,
`sink-templates/`, `tests/`).

## What CI runs on every hub PR

```bash
faucet hub lint --hub .
faucet template test tests/<owner>/<name>/suite.yaml     # each tests/*/*/suite.yaml
python3 scripts/replay.py                                 # each tests/*/*/replay.yaml
faucet hub list --hub . --json                           # then hub check for every source x sink
faucet hub check --hub . --source <owner>/<name> --sink faucet-hq/jsonl
```

Every source must compose with **at least two** sinks in the catalog. CI also
checks that `index.json` is current (see
[versioning-and-publishing.md](versioning-and-publishing.md)).

Also run `faucet validate`. It deserializes each stream's connector config,
which the lint, `hub check` and the suite do not do:

```bash
faucet validate --hub . --source <owner>/<name> --sink faucet-hq/postgres
```

## `faucet hub lint`

```bash
faucet hub lint --hub .                                         # whole catalog
faucet hub lint --hub . source-templates/<owner>/<name>.yaml    # one file
faucet hub lint --hub . --json
```

Findings name the field and the rule. For example:

```text
- `source.config.auth.config.token` holds a literal value — credentials must be `${param.NAME}` / `${env:NAME}` / `${secret:NAME}`
- param `api_token` looks like a credential but is not `secret: true`
```

The lint refuses literal credentials (including passwords inside connection
URLs), credential-looking params without `secret: true`, secret params with a
non-empty default, private hosts (`.internal`, managed-database hostnames),
placeholder text (`REPLACE_ME`, `xxx`, `your-token-here`), a missing
`description`, `name` not equal to the file stem, duplicate stream names, and
`${param.*}` references that are not declared.

## Suite file (`tests/<owner>/<name>/suite.yaml`)

Schema: `faucet schema template-test`. A suite for a source template names a
sink template. Every case composes, binds params, expands and compiles the
transforms exactly as a real run would, without network or data.

```yaml
# tests/acme/example-api/suite.yaml
version: 1
template: source-templates/acme/example-api.yaml   # a path (relative to where you run it) or a registered id
sink: sink-templates/faucet-hq/sqlite.yaml         # a path when template is a path
# overlay: tests/state-overlay.yaml                # optional deployment overlay
suite:
  auto:
    required_omitted: true     # one case per required param, omitted, expecting a clean failure
    enum_coverage: true        # one case per value of every `values:` param
    defaults_baseline: true    # the all-defaults combination
  cases:
    - name: defaults
      params: { api_token: suite_token }
    - name: rejects-undeclared-page-size
      params: { api_token: suite_token, page_size: "1000" }
      expect: { error: page_size }       # must fail AND mention this substring
  combine:                               # optional generated product
    params:
      page_size: ["50", "250"]
      api_base_url: ["https://api.example.com/v1", "https://eu.api.example.com/v1"]
    pairwise: false
  behavioral:
    - name: customer-record-shape
      params: { api_token: suite_token }
      input:
        - { id: 1, displayName: Ada, address: { city: Paris, zip: "75001" } }
      expect:
        records:
          - { id: 1, display_name: Ada, address: '{"city":"Paris","zip":"75001"}' }
```

Notes:

- Use fake values for secret params in suites (`suite_token`, `ghp_suite`).
  They are never sent anywhere.
- `auto` cases are derived from the template's own `params:`, so they stay
  correct as params are added. Required params that a `combine:` sweep does not
  name are filled in automatically.
- `expect` for validation cases is `{ valid: true }` (the default) or
  `{ error: "<substring>" }`.
- `behavioral` cases feed `input` records (inline, or a path to a `.jsonl` /
  `.json` / `.yaml` fixture) through the pipeline and use `faucet test`'s
  expectations: `records` (exact, in order, or with `unordered: true`),
  `records_written`, `dlq`, `dlq_count`, `error`. They test transforms
  (`keys_case`, `json_encode`, ...), not HTTP.
- Guard rails: generation stops at 512 cases, an empty suite is an error, and
  duplicate case names are an error.

Run it:

```bash
faucet template test tests/acme/example-api/suite.yaml
faucet template test tests/acme/example-api/suite.yaml --filter 'auto:*'
faucet template test tests/acme/example-api/suite.yaml --json
```

```text
template source-templates/acme/example-api.yaml
  ok   [explicit] defaults
  ok   [explicit] rejects-undeclared-page-size
  ok   [auto] auto:defaults
  ok   [auto] auto:page_size=50
  ok   [auto] auto:missing-api_token
  ok   [behavioral] customer-record-shape
```

The exit code is the number of failed cases. `--filter` takes `*` wildcards,
and a bare name is an exact match.

## Recorded replays (`tests/<owner>/<name>/replay.yaml`)

The hub's `scripts/replay.py` serves recorded HTTP exchanges on a local port.
It runs `faucet run --hub . --source <owner>/<name> --sink faucet-hq/jsonl`
against them and compares each stream's records with
`expected/<stream>.jsonl`. It fails when a request matches no exchange, when an
exchange is never requested, or when records differ. This is the only offline
test of pagination, auth headers and incremental binds.

```yaml
# tests/acme/example-api/replay.yaml
runs: 2                              # second run proves incremental streams resume
overlay: ../../state-overlay.yaml    # the hub's file-state overlay
params:
  api_base_url: "{replay}"           # why every host must be a param
  api_token: tok_replay
exchanges:
  - match:
      path: /invoices
      query: { updated_since: "2020-01-01T00:00:00Z" }
    response:
      body: { data: [{ id: 10, updated_at: "2026-02-02T00:00:00Z" }], next_cursor: null }
  - match:
      path: /invoices
      query: { updated_since: "2026-02-02T00:00:00Z" }    # only requested if the bookmark advanced
    response:
      body: { data: [], next_cursor: null }
```

Matchers: `method`, `path`, `query`, `absent`, `headers`, `body` (JSON subset),
`body_regex`, `form`. Responses take `status`, `headers` and `body` (or `text`).
`{replay}` is replaced with the server URL in responses too, so next-page links
work.

```bash
python3 scripts/replay.py acme/example-api
python3 scripts/replay.py --update acme/example-api    # rewrite expected/, then read the diff
```

Record from the API reference or a sandbox account, and replace every id,
name, email and token with synthetic values before you commit. The script runs
`faucet` from `PATH`. Set `FAUCET=/path/to/faucet` to use another binary.

A complete, passing set is in [../examples/](../examples/tests/acme/example-api/suite.yaml):
the template, `suite.yaml`, `replay.yaml` and `expected/`.

## Live smoke test

Before opening the PR, run once against the real API into local files:

```bash
faucet run --hub . --source <owner>/<name> --sink faucet-hq/jsonl \
  --param api_token="$API_TOKEN" --limit 100
mkdir -p out    # the sqlite sink does not create the directory of ./out/faucet.db
faucet run --hub . --source <owner>/<name> --sink faucet-hq/sqlite \
  --overlay tests/state-overlay.yaml --param replay_state_dir=./.state \
  --param api_token="$API_TOKEN"
```

Run the sqlite command twice. The second run should request only newer
records for incremental streams.
