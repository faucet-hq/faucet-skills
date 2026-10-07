---
name: faucet-deploy
description: >-
  Use when deploying faucet to production or running it unattended: running
  pipelines on cron, systemd, Airflow, Dagster or Kubernetes (Job, CronJob,
  Deployment), using `faucet schedule`, standing up `faucet serve` with auth,
  RBAC, audit and run history, installing the Helm chart or choosing a
  container image, choosing a durable state backend (file, redis, postgres),
  wiring Prometheus, OpenTelemetry, Grafana dashboards and alerts, setting up
  event triggers, and hardening a deployment (TLS, secrets, log levels,
  network binds, least privilege).
license: Apache-2.0
---

# Deploying faucet

`faucet` runs a pipeline config three ways: once (`faucet run`), on its own
cron (`faucet schedule`), or as an HTTP control plane (`faucet serve`). All
three read the same config and the same state store. This skill is how to pick
one, run it so it survives restarts and upgrades, and know when it breaks.

Writing the pipeline config itself is the `faucet-pipelines` skill. Fixing a
failed or stuck pipeline is the `faucet-debug` skill.

## Before you start

Check that `faucet` is on the `PATH` with `faucet --version`. If it is missing, install it with either:

```bash
# Homebrew (macOS / Linux)
brew install faucet-hq/faucet-stream/faucet-cli

# Installer script (macOS / Linux)
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/faucet-hq/faucet-stream/releases/latest/download/faucet-cli-installer.sh | sh
```

For servers and clusters, the alternative is a container image
(`ghcr.io/faucet-hq/faucet-stream:<version>-<profile>`) or the Helm chart
(`oci://ghcr.io/faucet-hq/charts/faucet-stream`). See
[kubernetes-and-helm](references/kubernetes-and-helm.md).

## Which runtime

| Situation | Runtime | Reference |
|---|---|---|
| A step in an Airflow / Dagster DAG, or cron / systemd timer | `faucet run` (exit code = success) | [choosing-a-runtime](references/choosing-a-runtime.md) |
| Kubernetes owns the timer | CronJob running `faucet run` | [kubernetes-and-helm](references/kubernetes-and-helm.md) |
| One pipeline on a timer, no orchestrator | `faucet schedule` under systemd, or a 1-replica Deployment | [schedule](references/schedule.md) |
| Many pipelines, API callers, ad-hoc runs, RBAC + audit, web console | `faucet serve` | [serve](references/serve.md) |
| Runs fired by a webhook, an object landing, a queue filling | `faucet serve --triggers` (needs a build or image) | [triggers-and-events](references/triggers-and-events.md) |
| One-off backfill or migration | `faucet backfill` / `faucet run` as a Job | [choosing-a-runtime](references/choosing-a-runtime.md) |

Whatever you pick, **one row must never run twice at the same time**. The run
lease does not stop a second process in 1.13.2. Let the scheduler enforce it:
`overlap_policy: skip`, `concurrencyPolicy: Forbid`, Airflow
`max_active_runs=1`, `flock` around cron.

## What the prebuilt binary has

The Homebrew / installer binary (1.13.2) is the default build plus `serve`,
`serve-ui`, `schedule`, `lineage` and `templates`. Verified gaps:

| Feature | In prebuilt binary | How to get it |
|---|---|---|
| redis / postgres state, Prometheus | yes | |
| `faucet serve` with in-memory history, web console | yes | |
| `serve --history sqlite:…` / `postgres://…`, `--cluster`, persistent template registry | **no** (startup error) | `cargo install faucet-cli --features serve,serve-ui,schedule,templates,lineage,serve-history-postgres,serve-history-sqlite`, or any published image |
| `serve --triggers` | **no** | `--features triggers` (+ `triggers-object-store`, `triggers-redis`, `triggers-kafka`); images: webhook in all, every watcher in `-full` |
| `serve --mcp` | **no** (`/mcp` returns 404) | `--features mcp`, or any published image |
| `notifications:`, `catalog:` config blocks | **no** (`unknown field`) | `--features notify` / `catalog`, or any published image |
| `observability.otel` | **no** (validates, then export is disabled with a warning) | `--features otel`; no published image has it |
| `${vault:…}` / `${aws-sm:…}` / `${gcp-sm:…}` / `${azure-kv:…}` | **no** (load-time error) | `--features secrets` (or one `secrets-*`); no published image has it |
| `file` state `encryption:` | **no** (run fails) | `--features encryption` |
| `--templates-sync`, tenants | **no** | `--features templates-sync`, `tenants` |

`--features full` builds everything. This gap is tracked in
faucet-hq/faucet-stream#820.

## Checklist: `faucet run` from an orchestrator, cron or CronJob

1. `faucet validate --no-secrets --no-env-file <config>` in CI, with
   placeholder values for every `${env:…}`.
2. Durable state: `postgres` or `redis` (or `file` on a disk that comes back).
3. Invoke with `--no-env-file --log-format json --output json`; read the exit
   code and the stdout summary.
4. Serialize runs of the same config (see above). Set a timeout below the
   period (`activeDeadlineSeconds`, `--max-duration-secs`).
5. Credentials from platform secrets as env vars or mounted files.
6. Alert on task / Job failure; `faucet status <config>` exits 0 / 1 / 2 for
   healthy / degraded / failed.

```bash
faucet run /etc/faucet/orders.yaml --no-env-file --log-format json --output json
```

## Checklist: `faucet schedule`

1. A `schedule:` block: `cron`, `timezone`, `overlap_policy: skip`,
   `max_consecutive_failures`, `run_timeout_secs`, `shutdown_grace_secs`.
   Example: [scheduled-pipeline.yaml](examples/scheduled-pipeline.yaml).
2. A supervisor that restarts on a non-zero exit (systemd
   `Restart=on-failure`, or a Deployment with `replicas: 1`,
   `strategy: Recreate`).
3. `terminationGracePeriodSeconds` / systemd `TimeoutStopSec` above
   `shutdown_grace_secs`.
4. `observability.prometheus.listen` and alerts on heartbeat age and
   consecutive failures.
5. SIGHUP reloads the config in place; an invalid file is rejected and the old
   one keeps running.

```bash
faucet schedule /etc/faucet/orders.yaml --no-env-file --log-format json
```

## Checklist: `faucet serve`

1. Use a published image or a build with `serve-history-postgres` for
   production. The prebuilt binary keeps runs, audit and templates in memory.
2. Auth: `--auth-config` (RBAC) or the `FAUCET_SERVE_READ_TOKEN` /
   `FAUCET_SERVE_WRITE_TOKEN` / `FAUCET_SERVE_ADMIN_TOKEN` trio. In 1.13.2 the
   `--auth-config` tokens are **literal**: `${env:…}` is not expanded and
   becomes the token. Use generated values in a Secret-mounted file.
   Example: [serve-auth.yaml](examples/serve-auth.yaml).
3. `--history postgres://…`; `--cluster` when more than one replica.
4. Probes: liveness `/healthz`, readiness `/readyz`.
5. TLS at the ingress; `/metrics` restricted at the network layer; egress
   restricted (submitted configs run with the server's identity).
6. Never `--no-auth` beyond `127.0.0.1`, and never run the 1.13.2 image with
   its default command (`serve --no-auth`).

```bash
faucet serve --listen 0.0.0.0:8080 --auth-config /etc/faucet-auth/auth.yaml --history "$FAUCET_HISTORY_URL" --cluster --log-format json
```

## Kubernetes and Helm

- CronJob for `faucet run`: [k8s-cronjob.yaml](examples/k8s-cronjob.yaml).
- Helm override (serve with RBAC + postgres history + cluster, plus a
  CronJob): [helm-values.yaml](examples/helm-values.yaml).
- The chart's Job and CronJob mount an `emptyDir`: `file` state is lost after
  every run there. Use redis or postgres.
- Pin `image.tag` to `<version>-<profile>` and list your connectors under
  `connectors.sources` / `connectors.sinks` so a wrong image fails at start.

Details: [kubernetes-and-helm](references/kubernetes-and-helm.md),
[state-backends](references/state-backends.md).

## Observability

- Prometheus: `observability.prometheus.listen` (not `listen_addr`) for
  pipelines; serve exposes `/metrics` on its own port.
- Grafana dashboards and Prometheus rules ship in the faucet-stream repository
  under `observability/grafana/` and `observability/prometheus/alerts.yml`.
- Alert at least on: failed runs (`faucet_pipeline_runs_total{status="err"}`),
  scheduler heartbeat age, consecutive schedule failures, bookmark staleness,
  source lag, rows going to the DLQ (`faucet_sink_dlq_records_total`), and
  serve history degraded.

Details and alert rules: [observability](references/observability.md).

## Production-readiness checklist

- [ ] `faucet validate --no-secrets --no-env-file` passes in CI for every config
- [ ] `faucet doctor <config>` passes at deploy time (connectors and state store reachable)
- [ ] Durable state backend: postgres or redis, or `file` on a persistent single-writer disk; never `memory`
- [ ] Only one run of a given config at a time, enforced by the scheduler
- [ ] Prometheus endpoints bound to localhost or reachable only by Prometheus; serve `/v1` behind auth
- [ ] Alerts on run failure, bookmark staleness or source lag, and DLQ growth
- [ ] `faucet state export <config> -o <file>` and `faucet migrate --state <config> --check` before every upgrade
- [ ] `--no-auth` never exposed; tokens generated and stored as secrets
- [ ] Secrets only through `${env:…}` / `${file:…}` from the platform; `--no-env-file` set
- [ ] `--log-format json`, `--log-level info`
- [ ] Binary and image versions pinned; features you rely on confirmed present (`faucet list`, a dry start)

```bash
faucet doctor /etc/faucet/orders.yaml
faucet state export /etc/faucet/orders.yaml -o orders-state-backup.json
faucet migrate --state /etc/faucet/orders.yaml --check
```

Hardening detail: [hardening](references/hardening.md).
