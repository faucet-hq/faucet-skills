# Source-template anatomy

Schema: `faucet schema source-template`. The file lives at
`source-templates/<owner>/<name>.yaml`. A working example that passes the hub's
lint, checks and suite is in
[../examples/source-templates/acme/example-api.yaml](../examples/source-templates/acme/example-api.yaml).

```yaml
kind: source-template
name: example-api              # == file stem, ^[a-z0-9][a-z0-9_-]*$
owner: acme                    # == the directory it lives in (a GitHub login)
description: Example API — customers, invoices and an events log   # one line, required
tags: [example, rest]
docs: https://api.example.com/docs          # optional link to the public API docs
params:
  api_token:    { type: string, required: true, secret: true, description: "API token" }
  api_base_url: { type: string, default: "https://api.example.com/v1", description: "API root" }
source:                        # the connector every stream shares
  type: rest
  config:
    base_url: "${param.api_base_url}"
    auth: { type: bearer, config: { token: "${param.api_token}" } }
    records_path: $.data[*]
    pagination: { type: Cursor, next_token_path: $.next_cursor, param_name: cursor }
transforms:                    # runs before EVERY sink
  - { type: keys_case, config: { mode: snake } }
streams:
  - name: customers
    source: { config: { path: /customers } }   # deep-merged onto source.config
    primary_keys: [id]
    write: [overwrite, upsert]
```

## Top-level fields

| Field | Notes |
|---|---|
| `kind` | `source-template` |
| `name` | Must equal the file stem. The hub id is `owner/name`, and it becomes the composed pipeline's `name:`, so state keys are `owner/name::stream`. |
| `owner` | Your GitHub user or org login. Must equal the directory. The official set is `faucet-hq`. |
| `description` | Required by the lint. One line: the system, its API version, and what the streams cover. |
| `tags`, `docs` | Discovery tags and an upstream docs link. |
| `params` | Same grammar as a pipeline's `params:` (see below). |
| `auth` | Optional shared auth catalog. Connectors point at an entry with `auth: { ref: <name> }`. |
| `source` | `{ type, config }` for the main connector. `type` is a source connector name (`rest`, `graphql`, `csv`, ...). |
| `sources` | Extra named connectors for a second endpoint family (a reports API beside the entity API). A stream uses one with `source: { ref: <name> }`. The name `default` is reserved. |
| `transforms` | Shared shaping applied before every sink, so all destinations get the same record shape. |
| `contract` | Optional data contract, passed through as `pipeline.contract`. |
| `streams` | One entry per destination table. |

## Params

```yaml
params:
  api_token:  { type: string, required: true, secret: true, description: "…" }
  start_date: { type: string, default: "2020-01-01T00:00:00Z", description: "…" }
  region:     { type: string, default: us, values: [us, eu], description: "…" }
```

| Field | Meaning |
|---|---|
| `type` | `string` (default), `int`, `float`, `bool`. When `${param.X}` is a value's *entire* text the typed value is substituted: `int` 100 becomes the JSON number `100`. Embedded in a longer string it is stringified. |
| `required` / `default` | Mutually exclusive. |
| `secret` | Redacted from logs and errors. Required for credentials, and it must have no default. The lint flags credential-looking names without it. |
| `values` | Closed set. Bad values fail at bind time, and suites can sweep every value. |
| `description` | Shown by `faucet hub check` and `faucet template show`. |
| `computed` | Derived from other params via interpolation. See `faucet schema source-template`. |

Reference a param as `${param.NAME}` anywhere in `source`, `sources`, `auth`,
or a stream. Write `$${param.x}` for a literal `${param.x}`.

Two rules from the hub's own templates:

- Make **every host** a param that defaults to the public URL (`api_base_url`,
  `github_api_url`). Tests and replays point it at a local server, and
  self-hosted variants of the API work without a fork.
- Watch types. A `type: int` param used as a whole `query_params` value becomes
  a number, and the REST connector rejects it ("expected a string"). Declare such
  params `type: string`, or embed them in a longer string. `faucet validate`
  catches this. `faucet hub check` and the suite do not.

## Streams

| Field | Notes |
|---|---|
| `name` | `^[a-z0-9][a-z0-9_]*$` (no hyphens). Becomes the matrix row id, the state-key suffix, and `${stream}` in the sink's `per_stream`, which is usually the table name. |
| `description` | Optional. |
| `source` | `{ config: {...} }` deep-merged onto the shared `source.config`, and/or `{ ref: <name> }` to read through one of `sources:`. |
| `transforms` | Per-stream transforms, applied **after** the shared ones. |
| `inherit_transforms` | `false` drops the shared transforms for this stream. |
| `primary_keys` | Destination key columns, named as they look **after** transforms. Required for `upsert` and `delete`. They become the sink's `key`. |
| `write` | A mode or a preference list from `overwrite`, `upsert`, `append`, `delete`. The default is `append`. |
| `parent` / `parent_key` | Run this stream once per record of the parent stream. `${<parent>.<field>}` tokens resolve per parent record, and `parent_key` (default `id`) keys each child's state. |

Choosing `write`:

| Data | `write` |
|---|---|
| Full refresh of a dimension or list (re-read every run) | `[overwrite, upsert]` |
| Incremental feed of mutable records | `[upsert, append]` |
| Immutable event log | `append` |

Order matters. The composer takes the first mode the sink supports, so
`[overwrite, upsert]` gives an atomic replace on a warehouse and still composes
with a file sink (through its `overwrite→append` alias).

Child streams (`parent:`) are refused on sinks that truncate per invocation,
and their `overwrite` cannot be satisfied through an alias. List a fallback
(`[overwrite, upsert]`) so they still compose on those sinks.

## Shared transforms

Put shaping that every destination needs in the top-level `transforms`:

- `keys_case` (`mode: snake`) gives one column style everywhere.
- `json_encode` (`fields: [...]`) turns nested objects and arrays into JSON
  strings so every sink can store them. It is usually per-stream, because the
  nested fields differ per endpoint.
- `flatten` (`separator: "_"`) and `cast` cover the rest.

`faucet schema transform <name>` prints each transform's config. Remember that
`primary_keys` use post-transform names, while `replication_key` uses the raw
API field name (see [rest-patterns.md](rest-patterns.md)).

## Shared auth catalog

When several connectors (or `source` plus `sources`) share one token, or the
flow is more than a static header, declare it once at the top level and
reference it:

```yaml
auth:
  api:
    type: token_endpoint
    config:
      url: "${param.api_base_url}/oauth/token"
      method: POST
      encoding: form
      body: { grant_type: client_credentials, client_id: "${param.client_id}", client_secret: "${param.client_secret}" }
      token_path: "$.access_token"
      expiry_path: "$.expires_in"
source:
  type: rest
  config:
    base_url: "${param.api_base_url}"
    auth: { ref: api }
```

Provider types: `static`, `oauth2` (client credentials), `oauth2_refresh`
(refresh-token flow), `token_endpoint` (with optional
`apply_as: { header, template }` for a non-Bearer header), and
`google_service_account`. The hub's official templates use the last three. A connector's `auth:` is either inline or a `{ ref }`,
never both.

## Second endpoint family (`sources:`)

```yaml
sources:
  reports:
    type: rest
    config:
      base_url: "${param.api_base_url}/reports"
      auth: { type: bearer, config: { token: "${param.api_token}" } }
streams:
  - name: daily_usage
    source: { ref: reports, config: { path: /usage } }
    primary_keys: [date]
    write: [upsert, append]
```

## Companion files in the hub

| File | Purpose |
|---|---|
| `<name>.faucet.yaml` | Sidecar: `launch`, `stable`, `deprecated`, catalog `description` (see [versioning-and-publishing.md](versioning-and-publishing.md)). |
| `<name>.md` | README for the template: required scopes, run times, changelog. Required for `faucet-hq/`, recommended for others. |
| `tests/<owner>/<name>/suite.yaml` | `faucet template test` suite (see [testing.md](testing.md)). |
| `tests/<owner>/<name>/replay.yaml` + `expected/` | Recorded HTTP exchanges for `scripts/replay.py`. |
| `source-templates/<owner>/OWNERS` | Who may change the namespace, by numeric GitHub id. |
