---
name: faucet-templates
description: >-
  Use when working with faucet Template Hub templates: running a hub template with
  `faucet run --source <owner>/<name> --sink <owner>/<name>`, picking between
  templates for the same system, composing a source template with a sink template
  and checking the pairing, writing a new source-template for an HTTP or GraphQL
  API, writing a sink-template, adding streams or params to a template, testing a
  template with a `faucet template test` suite, versioning a template (stable,
  preview, deprecating a version), or publishing a template to
  github.com/faucet-hq/template-hub.
license: Apache-2.0
---

# faucet Template Hub templates

The Template Hub splits a pipeline in two:

- A **source-template** describes one system once: connector (`rest`, `graphql`,
  `csv`, ...), auth, pagination, shared transforms, and a list of **streams**.
  Each stream is one destination table and declares the write modes it accepts.
- A **sink-template** describes one destination and how each stream is addressed
  (`per_stream`, e.g. `table_id: "${stream}"`).

At run time `faucet run --source X --sink Y` composes the two into an ordinary
pipeline config. For each stream the composer picks the first mode in its
`write` list that the sink supports. A template's id is `owner/name`, and a bare
name means `faucet-hq/name`, the official namespace. The default catalog is
`github:faucet-hq/template-hub`. `--hub`, `$FAUCET_HUB` or a local `./hub`
directory override it.

`faucet hub ...` (this skill) works on files in a catalog. `faucet template ...`
is a different tool: a registry (`--store sqlite:...`) of parameterized configs
that `faucet serve` can trigger over HTTP. Hub templates can be registered
there, but publishing to the hub never involves `faucet template register`.
The one exception is `faucet template test`, which is the hub's suite runner.

## Before you start

Check that `faucet` is on the `PATH` with `faucet --version`. If it is missing, install it with either:

```bash
# Homebrew (macOS / Linux)
brew install faucet-hq/faucet-stream/faucet-cli

# Installer script (macOS / Linux)
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/faucet-hq/faucet-stream/releases/latest/download/faucet-cli-installer.sh | sh
```

## Workflow 1: use a template

1. **Find it.**
   ```bash
   faucet hub list                      # every source and sink in the default hub
   faucet hub list --sort stars         # or --sort updated
   faucet hub matrix                    # source x sink compatibility table
   faucet hub rows faucet-hq/github     # the streams a source template produces
   ```
   If several namespaces publish the same system, prefer the official
   `faucet-hq/` one. Otherwise compare the `trust` signals in `index.json`
   (stars, `updated`, `stable_since`, `open_issues`, `compatible_sinks`).
   Stars measure popularity, not correctness.

2. **Check the pairing** before you run anything:
   ```bash
   faucet hub check --source faucet-hq/github --sink faucet-hq/postgres
   ```
   It prints each stream's chosen mode (`upsert (key: id)`, `overwrite→append`)
   and a ready `faucet run` line that lists the required params. It exits
   non-zero when any stream has no viable mode.

3. **Validate offline, then run.** Get secrets from a secret manager into
   environment variables and pass them with `--param`:
   ```bash
   # GITHUB_TOKEN and PG_URL are exported by your secret store / CI secrets
   faucet validate --source faucet-hq/github --sink faucet-hq/sqlite
   faucet run --source faucet-hq/github --sink faucet-hq/postgres \
     --overlay ops/prod.yaml \
     --param github_owner=octo-org --param github_repo=demo \
     --param github_token="$GITHUB_TOKEN" --param pg_url="$PG_URL"
   ```
   Param values are literal. `--param x='${env:X}'` is refused, so let the
   shell expand the variable. Params marked `secret: true` are redacted from
   logs and errors.

4. **Give incremental streams a state store** with a deployment overlay
   (`kind: deployment`, passed with `--overlay`). Without one, the run re-reads
   everything every time, and `faucet validate` warns about it.

5. **Pin for production**: `--source acme/erp@3` (exact version), `@stable`
   (the default) or `@newest` (the newest version that is not deprecated).
   Pins work only against a catalog with version history, such as the public
   hub. A local directory hub refuses them.

Details: [references/using-templates.md](references/using-templates.md).

## Workflow 2: author and publish a template

1. **Start from the closest existing template.** Pick the one with the same
   connector, auth and pagination style. In the hub: `faucet-hq/example-rest-api`
   (a skeleton), `faucet-hq/github` (Link header, `since` incremental),
   `faucet-hq/stripe` (cursor), `faucet-hq/shopify` (GraphQL),
   `faucet-hq/google-analytics-4` (windowed reports). A minimal, tested
   example is in [examples/](examples/source-templates/acme/example-api.yaml).
   Put it at `source-templates/<your-login>/<name>.yaml` with
   `owner: <your-login>` and `name:` equal to the file stem.

2. **Fill the shared source, then one stream per table.** Read the connector
   schema rather than guessing field names:
   ```bash
   faucet schema source rest
   faucet schema source graphql
   faucet schema source-template
   ```
   For each stream set `name`, a `source.config` override (usually `path`),
   `primary_keys`, and `write`: `[overwrite, upsert]` for a full refresh,
   `[upsert, append]` for an incremental feed, `append` for an event log.
   Put shaping that every sink needs (`keys_case`, `json_encode` for nested
   objects) in the top-level `transforms`.
   See [references/source-template-anatomy.md](references/source-template-anatomy.md)
   and [references/rest-patterns.md](references/rest-patterns.md).

3. **Make every host and secret a param.** Credentials are `${param.NAME}` with
   `secret: true` and no default. Put the API root in a param that defaults to
   the public URL, so tests and replays can point it at a local server.

4. **Lint, check, validate** from the hub root:
   ```bash
   faucet hub lint --hub .
   faucet hub check --hub . --source <your-login>/<name> --sink faucet-hq/jsonl
   faucet hub check --hub . --source <your-login>/<name> --sink faucet-hq/bigquery
   faucet validate --hub . --source <your-login>/<name> --sink faucet-hq/postgres
   ```
   CI requires every source to compose with at least two hub sinks. `faucet
   validate` also deserializes each stream's connector config, which
   `faucet hub check` and the suite do not do (for example, a `type: int` param
   used as a whole `query_params` value fails only here).

5. **Write the suite** at `tests/<your-login>/<name>/suite.yaml` and run it:
   ```bash
   faucet template test tests/<your-login>/<name>/suite.yaml
   ```
   The exit code is the number of failed cases. Run the template once against
   the real API too, with `--sink faucet-hq/jsonl`.
   See [references/testing.md](references/testing.md).

6. **Version and publish** with a pull request to faucet-hq/template-hub that
   only touches your namespace. The first PR into a namespace adds its `OWNERS`
   file. Regenerate `index.json` in the PR
   (`faucet hub matrix --hub . --format json > index.json`). Each merged change
   to a template's meaning becomes the next version (v1, v2, ...). A sidecar
   `<name>.faucet.yaml` controls `stable`, previews and deprecation.
   See [references/versioning-and-publishing.md](references/versioning-and-publishing.md).

Writing a destination instead: [references/sink-template-anatomy.md](references/sink-template-anatomy.md).

## Hard rules

- **No secrets in templates.** Every credential is a param with `secret: true`
  and no default, referenced as `${param.NAME}` (`${env:...}` / `${secret:...}`
  also pass the lint). Values come from a secret store at run time. Never write
  a literal token, a private hostname, or placeholder text such as `REPLACE_ME`.
  `faucet hub lint` refuses all of these.
- **A published version is immutable.** `@3` always means the same body. Never
  rewrite git history or edit a version in place. To fix it, merge the fix (it
  becomes the next version). To roll back, re-commit the older body (it becomes
  a new version) and point `stable` at it.
- **A change in meaning gets a new version.** Renaming a stream, changing
  `primary_keys` or `write`, or removing a param is a new version, never a
  silent edit. Comment-only and whitespace-only edits do not create a version.
- **Keep `stable` on the proven version.** Ship risky changes with
  `launch: false` in the sidecar (a preview reachable with `@newest`), then move
  `stable` once the change is proven. You cannot deprecate the stable version.
  Deprecate others with a reason that names the replacement.
- **Name things after the system, not your company.** Use public endpoints
  only, and give each stream a correct write preference. `upsert` needs
  `primary_keys`.
- **Sink templates never set `write_mode` or `key`.** The composer injects them
  per stream.

## Reference files

- [references/using-templates.md](references/using-templates.md): find, check,
  compose, params, secrets, overlays, private hubs, version pins.
- [references/source-template-anatomy.md](references/source-template-anatomy.md):
  every source-template field, streams, `sources:`, `parent:`, shared auth.
- [references/sink-template-anatomy.md](references/sink-template-anatomy.md):
  `per_stream`, `write_mode_aliases`, child-stream rules.
- [references/rest-patterns.md](references/rest-patterns.md): auth, pagination,
  `records_path`, incremental keys and binds, windows, rate limits, GraphQL.
- [references/testing.md](references/testing.md): suite format, auto cases,
  behavioral cases, replays, what CI runs.
- [references/versioning-and-publishing.md](references/versioning-and-publishing.md):
  versions, sidecars, deprecation, namespaces, the PR flow, stars.
