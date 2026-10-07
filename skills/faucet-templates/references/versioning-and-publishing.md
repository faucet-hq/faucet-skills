# Versioning and publishing to the hub

The public catalog is github.com/faucet-hq/template-hub. Its `README.md` and
`CONTRIBUTING.md` are the source of truth. This file summarizes them.

## Namespaces and ownership

- A template lives at `source-templates/<owner>/<name>.yaml` (or
  `sink-templates/...`). `owner:` in the file equals the directory, which is a
  GitHub user or org login in lowercase. The hub id is `<owner>/<name>`, so
  `acme/netsuite` and `octo/netsuite` can coexist.
- `faucet-hq/` is the official namespace, owned by the org like any other.
  Nothing lives at the top level.
- The first PR into a namespace adds an `OWNERS` file with **numeric** GitHub ids
  (logins can be renamed, ids cannot):

  ```yaml
  # source-templates/acme/OWNERS
  owners:
    - { login: acme-bot, id: 12345678 }
  ```

  Every later change to that namespace must come from a listed id. The required
  **Ownership** check compares this against the PR author. A PR that touches
  someone else's namespace fails. To add a colleague, an existing owner opens a
  PR that edits `OWNERS`. Sink templates have their own
  `sink-templates/<owner>/OWNERS`.

## Versions

- Versions are **computed from git history on `main`** by `scripts/index.py`
  (v1, v2, v3 ...). Nobody writes a version number.
- Each merged change to a template's **meaning** is the next version. Comment
  and whitespace changes fold into the previous version, because the parsed
  document is hashed.
- A published version **never changes**. `@3` always resolves to the same body.
  Selectors work only against a catalog with version history, such as the
  public hub.

| Change | What to do |
|---|---|
| Fix a bug | **Fix forward.** Merge the fix. It becomes the next version, and `stable` follows unless the sidecar holds it. |
| Breaking change (rename or remove a stream or param, change `primary_keys` or `write`, a different API version) | Ship it as a new version with `launch: false`. Users opt in with `@newest` or `@N`. Move `stable` once it is proven. Say what changed in the template's `.md` changelog. |
| A release went bad | **Roll back.** Re-commit the older body (it becomes a new version with the old content) and set `stable` to it. |
| A version must not be chosen any more | **Retire** it with `deprecated:` and a reason that names the replacement. |

Never force-push, rewrite history, or delete a version to "fix" it. Pinned users
depend on `@N` staying the same.

## The sidecar: `<name>.faucet.yaml`

It sits next to the template, e.g. `source-templates/acme/netsuite.faucet.yaml`:

```yaml
launch: false          # publish this change as a preview; stable stays where it is
# stable: 3            # or pin stable to an exact version
description: Acme's NetSuite — saved searches + ledger
deprecated:
  2: "drops the invoices stream; use v3+"
  1: "superseded"
```

| Key | Effect |
|---|---|
| `launch` | `true` (what the official templates use): every merged version becomes stable. `false`: new versions are previews, reachable with `@newest` or `@N`. |
| `stable` | Pin the stable version explicitly. |
| `deprecated` | Map of version to reason. `@N` still resolves and prints `warning: acme/erp v2 is deprecated: <reason> — stable is v4`. `@newest` skips deprecated versions, the hub page hides them, and a mirroring `faucet serve` skips a template whose newest version is deprecated. Un-deprecate by deleting the entry. |
| `description` | Catalog description. |

CI refuses a sidecar that deprecates the `stable` version (pinned or computed),
deprecates a version that does not exist, or uses a key that is not a positive
integer (`2`, not `v2`). Move `stable` to a live version before you deprecate
the old one.

## The PR

1. Fork faucet-hq/template-hub and add only files under your namespace:
   - `source-templates/<you>/<name>.yaml` (required)
   - `source-templates/<you>/OWNERS` (first PR into the namespace)
   - `source-templates/<you>/<name>.faucet.yaml` (optional sidecar)
   - `source-templates/<you>/<name>.md` (README: scopes, run times, changelog)
   - `tests/<you>/<name>/suite.yaml`, plus `replay.yaml` and `expected/`
     (required for `faucet-hq/`, encouraged everywhere)
2. Run what CI runs, from the repo root:

   ```bash
   faucet hub lint --hub .
   faucet hub check --hub . --source <you>/<name> --sink faucet-hq/jsonl
   faucet hub check --hub . --source <you>/<name> --sink faucet-hq/bigquery
   faucet template test tests/<you>/<name>/suite.yaml
   python3 scripts/replay.py <you>/<name>
   faucet hub matrix --hub . --format json > index.json
   ```

   The CI check that `index.json` is current compares everything except
   version, stable, live_versions and trust data. If it reports the file as
   stale, `python3 scripts/index.py` regenerates it (it needs `pyyaml`). After
   the merge, CI rewrites `index.json` on `main` with the version history.
3. Fill in the PR template: the kind, the system with a link to its public
   docs, and one line per stream (name, write preference, why). Confirm the
   checklist, including that you ran the template against the real system.
4. A maintainer reviews that the template matches the public API, that the
   write preferences fit the data, and that nothing private leaked. A green CI
   run is the bar. Templates are published under Apache-2.0 OR MIT. There is
   no CLA.

The hub website's **Publish** button opens a pre-filled new-file form in the
repository. It produces the same PR.

## Private catalogs

Any repository with the same layout is a hub. Point consumers at it with
`--hub github:org/catalog` (or `--source-hub`) and `GITHUB_TOKEN`. Versions,
`@N` pins and sidecars work the same way once that repository generates its own
`index.json`.

## Stars and trust signals

Each template gets a discussion in the hub's **Discussions → Templates**
category, opened automatically. A **star** is a distinct account that reacted
👍, ❤️ or 🚀 on that discussion. Each account counts once, and upvotes (↑) are
not counted. `scripts/stars.py` collects stars every hour and after each merge.
Never edit `stars.json` or the `trust` block by hand.

`index.json` gives each entry a `trust` block: `stars` / `star_url`, `updated`
/ `stable_since`, `compatible_sinks`, `open_issues` (issues labelled
`template:<id>`), and `publisher` (how many templates the namespace publishes,
and the account's age). `faucet hub list --sort stars|updated` uses the same
data. Stars measure popularity, not correctness. To report a problem with a
template, open an issue with its `template:<id>` label.
