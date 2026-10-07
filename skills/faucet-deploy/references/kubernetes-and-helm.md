# Kubernetes, containers and Helm

## Images

Connectors are compile-time features, so the image decides what can run.
Published images on GHCR, one per release and profile:

| Tag | Connectors | Runtime features |
|---|---|---|
| `ghcr.io/faucet-hq/faucet-stream:<v>-core` | sources `rest, postgres, s3, csv`; sinks `postgres, s3, jsonl, stdout` | observability, all state backends, serve + UI, schedule, serve history (postgres, sqlite), catalog, notify, lineage, templates, mcp, webhook triggers |
| `…:<v>-analytics` | sources `rest, postgres, s3, bigquery, snowflake`; sinks `bigquery, snowflake, s3, jsonl` | same as core |
| `…:<v>-cdc` | sources `postgres-cdc, mysql-cdc, mongodb-cdc`; sinks `postgres, kafka, s3` | same as core |
| `…:<v>-full` (also `:<v>` and `:latest`) | every first-party connector | core set + object-store, redis and kafka trigger watchers |

Notes, verified on `1.13.2-core`:

- Images are `linux/amd64` only, run as uid/gid `65532`, entrypoint `faucet`,
  and set `FAUCET_SERVE_LISTEN=0.0.0.0:8080`.
- **The default command of the 1.13.2 images is `serve --no-auth`.** A bare
  `docker run -p 8080:8080 <image>` publishes an unauthenticated control
  plane. Always pass an explicit command (`run …`, `schedule …`, or
  `serve` with auth).
- No published image has `otel`, `encryption`, `secrets-*` (`${vault:…}` etc.),
  `tenants` or `templates-sync`. For those, build from the repository
  Dockerfile with a raw feature list, for example
  `--build-arg FEATURES="observability,state,transforms,serve,serve-ui,schedule,serve-history-postgres,otel,secrets-vault,source-rest,sink-postgres"`.
- Pin `<version>-<profile>`. Never deploy `latest` or a bare profile tag.
- Confirm the connectors before you deploy:

```bash
docker run --rm ghcr.io/faucet-hq/faucet-stream:1.13.2-core list
docker run --rm ghcr.io/faucet-hq/faucet-stream:1.13.2-core schema sink postgres
```

## Shape per runtime

| Runtime | Kubernetes object | Must-haves |
|---|---|---|
| `faucet run` on a timer | CronJob | `concurrencyPolicy: Forbid`, `restartPolicy: Never`, `activeDeadlineSeconds` under the period, small `backoffLimit`, redis/postgres state |
| `faucet run` once (migration, backfill) | Job | same; `ttlSecondsAfterFinished` |
| `faucet schedule` | Deployment, `replicas: 1`, `strategy: Recreate` | `terminationGracePeriodSeconds` > `shutdown_grace_secs`; redis/postgres state (or a PVC for `file`) |
| `faucet serve` | Deployment + Service | auth, postgres history, readiness on `/readyz`, liveness on `/healthz`, `terminationGracePeriodSeconds` > `--shutdown-grace-secs`; `--cluster` for more than one replica |

Hand-written CronJob: [k8s-cronjob.yaml](../examples/k8s-cronjob.yaml). It
mounts the pipeline from a ConfigMap, credentials from a Secret via
`envFrom`, runs with a read-only root filesystem, and exits non-zero on a
failed row so the Job is marked failed.

`faucet run` exits when it finishes, so Prometheus usually misses a CronJob's
`/metrics`. For CronJobs, alert on Job failure (kube-state-metrics), on
`faucet status` exit codes, or on bookmark staleness read by a long-running
process. See [observability](observability.md).

## Secrets

- Put credentials in a Kubernetes Secret and load them with `envFrom` (or
  `env.valueFrom.secretKeyRef`). Reference them as `${env:VAR}` in the config.
- For file-shaped credentials (service-account JSON, client certificates),
  mount the Secret as a volume and use `${file:/var/run/secrets/faucet/key.json}`.
- Pass `--no-env-file` so a stray `.env` in the working directory is never
  loaded.
- `${vault:…}`, `${aws-sm:…}`, `${gcp-sm:…}`, `${azure-kv:…}` need a custom
  build with `secrets-*`. Otherwise sync those stores into Kubernetes Secrets
  with your platform's secret operator.

## PVC for `file` state

`file` state works in Kubernetes only when the same volume comes back for every
run and only one pod uses it at a time: a ReadWriteOnce PVC mounted at the
state path, `concurrencyPolicy: Forbid` (CronJob) or `replicas: 1` +
`strategy: Recreate` (Deployment). The Helm chart does not mount a PVC into
its Job or CronJob (they get an `emptyDir`), so with the chart use redis or
postgres state. See [state-backends](state-backends.md).

## Helm chart

Published as an OCI chart, versioned with the CLI:

```bash
helm show values oci://ghcr.io/faucet-hq/charts/faucet-stream --version 1.13.2
helm template faucet oci://ghcr.io/faucet-hq/charts/faucet-stream --version 1.13.2 -f helm-values.yaml
helm upgrade --install faucet oci://ghcr.io/faucet-hq/charts/faucet-stream --version 1.13.2 -n data -f helm-values.yaml
```

Minimal override: [helm-values.yaml](../examples/helm-values.yaml) (renders
cleanly with `helm template` against chart 1.13.2).

Values that matter:

| Value | Default | Set it to |
|---|---|---|
| `image.tag` | chart `appVersion` (an old release) | `<version>-<profile>`, always explicit |
| `connectors.sources` / `connectors.sinks` | `[]` | The connectors your configs use. An init container runs `faucet schema …` for each and fails the pod if one is missing |
| `pipelineConfig.existingConfigMap` | `""` | A ConfigMap you manage. Prefer it over inline `pipelineConfig.content` |
| `envFrom` / `env` | `[]` | Secret references. Shared by every pod the chart renders (serve, Job, CronJob) |
| `serve.auth.mode` | `token` (chart generates a token Secret) | `rbac` with `serve.auth.existingSecret` + `existingSecretKey` pointing at your auth file; never `none` outside a sandbox |
| `serve.history.backend` | `memory` | `postgres` (needed for `serve.cluster.enabled` and durable audit) |
| `serve.history.url` | `""` | Leave empty and pass `--history=$(VAR)` in `serve.extraArgs` with `VAR` from a Secret, so the DSN is not in values |
| `serve.cluster.enabled` | `false` | `true` when `serve.replicaCount` > 1 |
| `serve.preview.enabled` | `false` | Keep false outside dev |
| `serve.ui.enabled` | `true` | `false` if nobody uses the console |
| `cronjob.concurrencyPolicy` | `Forbid` | Keep it |
| `cronjob.timeZone` | `""` | Set it (`Etc/UTC`) so the schedule is not the controller's zone |
| `serviceMonitor.enabled` | `false` | `true` with the Prometheus Operator; scrapes serve's `/metrics` |
| `ingress.enabled` | `false` | Only with TLS on the ingress |

Chart security defaults are good and should stay: non-root 65532, read-only
root filesystem, all capabilities dropped, seccomp `RuntimeDefault`,
`automountServiceAccountToken: false`.

The chart fails to render when `job`/`cronjob` are enabled without a pipeline
config, when `serve.auth.mode` is unknown, when `rbac` has no principals, and
when `serve.cluster.enabled` uses the memory history.
