# `faucet serve`

A long-running HTTP control plane: callers submit pipeline configs, poll,
cancel and stream logs; the server runs them with bounded concurrency. In the
prebuilt binary (`serve`, `serve-ui`, `templates` are compiled in), but see
**What the prebuilt binary cannot do** below before you plan a production
deployment on it.

## Threat model in one paragraph

A submitted config runs with the **server's** identity. It resolves
`${env:…}` and `${file:…}` against the server's environment and filesystem, and
a REST source can be pointed at any address the server can reach (including
cloud metadata endpoints). In 1.13.2 a submitted config may also use the
`singer` source or sink, which starts a program on the host. So every token
with the `operator` role is effectively code and secret access on that host.
Hand out `viewer` by default, keep `operator` for orchestrators, run serve in
its own namespace with egress restricted, and never expose `--no-auth`.

## Auth: pick exactly one mode

Startup fails unless one of these is given (verified):

| Mode | How | Use |
|---|---|---|
| Single token | `FAUCET_SERVE_AUTH_TOKEN` env (or `--auth-token`) | One admin principal. Personal or single-purpose deployments |
| Token trio | `FAUCET_SERVE_READ_TOKEN` / `FAUCET_SERVE_WRITE_TOKEN` / `FAUCET_SERVE_ADMIN_TOKEN` (or `--read-token` / `--write-token` / `--admin-token`) | viewer / operator / admin with no file. Any subset; a reused or empty token is refused |
| RBAC file | `--auth-config auth.yaml` | Named principals, approvals policy, attribution in the audit log |
| None | `--no-auth` | Local development on `127.0.0.1` only |

Always pass tokens through environment variables or files, never on the
command line (`ps` shows flags).

`--no-auth` on `0.0.0.0` starts without any warning (verified on 1.13.2).
Nothing stops you; that is your job.

### The `--auth-config` file

Shape the code parses (`deny_unknown_fields`):
[serve-auth.yaml](../examples/serve-auth.yaml).

```yaml
principals:
  - { name: platform-admin, token: "<generated>", role: admin }
  - { name: airflow,        token: "<generated>", role: operator }
  - { name: grafana,        token: "<generated>", role: viewer }
approvals:            # optional, used with --require-approval
  expire_secs: 86400
  rules:
    - { kinds: [run], roles: [admin], min_approvers: 1, self_approve: false }
```

**1.13.2 reads `token` literally.** `token: "${env:ADMIN_TOKEN}"` is not
expanded: the token becomes the literal string `${env:ADMIN_TOKEN}`,
which anyone who has read the docs can send (verified). Write generated values
into the file and ship the file as a secret, or use the token trio env vars.
Releases after 1.13.2 resolve `${env:…}` / `${file:…}` here and refuse an
unresolved `${`.

Roles (verified with `GET /v1/whoami` and real requests):

| Role | Can |
|---|---|
| `viewer` | Read runs, logs, schemas, templates, status, changes. `POST /v1/runs` returns 403 |
| `operator` | viewer + submit / cancel / delete runs, trigger templates, fire triggers, doctor, DLQ replay/discard, propose and approve changes (as the approvals policy allows). `GET /v1/audit` returns 403 |
| `admin` | Everything: template lifecycle, `/v1/state/*`, `/v1/reload`, `GET /v1/audit`. Unclassified routes are admin-only |

`--require-approval run` (also `template_register`, `template_launch`) turns a
`POST /v1/runs` into a pending change request (`"status":"pending_approval"`)
that an approver allowed by `approvals.rules` must approve.

### Audit log

Every submit, cancel, delete and every denied request is recorded with
principal, role, action, run id, config fingerprint and source IP. Admins read
it:

```bash
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" 'http://127.0.0.1:8080/v1/audit?action=run.submit&limit=50'
```

With the default in-memory history the audit log is lost on restart.

## Run history

| `--history` | Survives restart | Multi-instance / `--cluster` | Needs feature |
|---|---|---|---|
| omitted (memory) | no | no | none |
| `sqlite:/var/lib/faucet/history.db` | yes (on a persistent volume) | one host only | `serve-history-sqlite` |
| `postgres://…` | yes | yes | `serve-history-postgres` |

If the database is unreachable, serve degrades instead of crashing:
`/readyz` returns 503 and `faucet_serve_history_degraded` is 1. Alert on it.
Terminal runs are kept for `--retain-terminal-runs-secs` (7 days), persisted
logs for `--log-retention-secs`.

`--cluster` (with a shared postgres history) makes N replicas pull pending
runs from the database and take over a dead replica's runs after
`--lease-ttl-secs`. Every replica must have the same env, secrets and
`--default-config`, because the claiming replica re-resolves `${…}` itself.

## What the prebuilt binary cannot do

Verified against the 1.13.2 release binary:

| You need | Prebuilt binary | Get it from |
|---|---|---|
| `--history sqlite:…` | `error: … requires building faucet with the serve-history-sqlite feature` | `cargo install faucet-cli --features serve,serve-ui,schedule,templates,serve-history-sqlite` or any published image |
| `--history postgres://…`, `--cluster` | refused the same way | `--features serve-history-postgres`, or any published image |
| `faucet template register --store sqlite:…` | refused; only `--store memory` works | same as above |
| `--triggers` | `--triggers requires a build with the triggers feature` | `--features triggers` (+ `triggers-object-store`, `triggers-redis`, `triggers-kafka`); see [triggers-and-events](triggers-and-events.md) |
| `--mcp` | flag accepted, `/mcp` returns 404 | `--features mcp`, or any published image |
| `--templates-sync` | refused | `--features templates-sync` (+ `templates-sync-object-store`); not in any published image |
| `notifications:` / `catalog:` config blocks | `unknown field` at validate | `--features notify` / `--features catalog`; published images include both |

Published images (`ghcr.io/faucet-hq/faucet-stream:<version>-<profile>`) are
built with `serve-history-postgres`, `serve-history-sqlite`, `mcp`, `notify`,
`catalog`, `templates` and `triggers` (webhook). Only `-full` also has the
object-store, redis and kafka trigger watchers. This gap is tracked in
faucet-hq/faucet-stream#820.

**Production serve therefore means a published image or a custom build, not
the prebuilt binary**, unless losing history, audit and templates on every
restart is acceptable.

## Essential flags

| Flag | Default | Notes |
|---|---|---|
| `--listen` / `FAUCET_SERVE_LISTEN` | `127.0.0.1:8080` | The images set `0.0.0.0:8080` |
| `--max-concurrent-runs` | `min(16, cpus)` | Total work is this × each config's `execution.max_concurrent` |
| `--max-queued-runs` | 8 × concurrent | Past it, `POST /v1/runs` returns 429 with `Retry-After` |
| `--default-config` | none | Merged under every submitted run. Pin `state:` and shared `auth:` here; reload with `POST /v1/reload` (admin) |
| `--shutdown-grace-secs` | `60` | Drain window on SIGTERM; set `terminationGracePeriodSeconds` higher |
| `--body-limit-bytes` | 1 MiB | |
| `--cors-origin` | off | Only if a browser app on another origin calls the API |
| `--callback-allow-host` | any host except link-local | Restrict per-run completion callbacks |
| `--no-ui` | UI on | Turns off the web console |
| `--preview-local-outputs` | off | Serves file contents over HTTP. Keep off outside dev |

## Web console

`GET /` serves the embedded console (`serve-ui`, in the prebuilt binary and the
images). The page itself is public; every `/v1` call it makes needs a token,
which the browser keeps in `localStorage`. Give people `viewer` tokens. Turn it
off with `--no-ui` where nobody needs it.

## HTTP API essentials

All `/v1/*` routes need `Authorization: Bearer <token>`. `/healthz`, `/readyz`
and `/metrics` never do.

| Call | Role | Purpose |
|---|---|---|
| `POST /v1/runs` | operator | Submit `{"config": "<yaml>", "name": …, "idempotency_key": …, "timeout_secs": …}` → 202 `{run_id, status}` |
| `GET /v1/runs/{id}` | viewer | Poll |
| `GET /v1/runs/{id}/logs` | viewer | SSE log stream, or `?format=jsonl` for persisted logs |
| `POST /v1/runs/{id}/cancel` | operator | Cooperative cancel at the next page boundary |
| `POST /v1/templates/{id}/runs` | operator | Run a registered template with `params` |
| `POST /v1/triggers/{name}` | operator | Fire a webhook trigger |
| `GET /v1/whoami` | any | Check a token's role |
| `GET /v1/audit` | admin | Audit log |
| `POST /v1/reload` | admin | Reload `--default-config` |

Send an `idempotency_key` from orchestrators: a retry with the same key and
body returns the original run id; the same key with a different body is 409.

Use a stable `name:` in every submitted config. It becomes the `pipeline`
metric label and the state-key prefix; a per-run name breaks resume and
explodes metric cardinality.

## Templates registry

`faucet template register|launch|promote …` and the `/v1/templates` routes
share one store. Point `faucet template … --store` and `faucet serve --history`
at the same URL. With the prebuilt binary only `--store memory` works, which
lasts for one process. Authoring templates is covered by the
`faucet-templates` skill.

## Probes

`/healthz` is liveness (200 while serving). `/readyz` is readiness: 503 while
history is degraded or the queue is full. Both are verified on 1.13.2.
