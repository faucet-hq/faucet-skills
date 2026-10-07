# Running, scheduling, backfilling

## One-shot runs

```bash
faucet run pipeline.yaml
faucet run pipeline.yaml --dry-run              # fetch from the source, write nothing
faucet run pipeline.yaml --limit 100            # stop after 100 records written
faucet run pipeline.yaml --clock 2026-06-01     # set the ${now.*} clock (RFC 3339 or a date)
faucet run pipeline.yaml --select orders        # run chosen matrix rows
faucet run pipeline.yaml --output json          # machine-readable summary on stdout (also ndjson)
faucet run pipeline.yaml --profile prod --param tenant_id=acme
faucet preview pipeline.yaml --limit 10         # source side only, records to stdout
```

Other run flags: `--concurrency N` (connector connections/fetches, not matrix
parallelism), `--max-records`, `--max-bytes`, `--max-duration-secs`,
`--allowed-sink` (merged with `budget:`), `--state-path` (file state only),
`--env-file`, `--no-env-file`, `--quiet`. With no config argument, `run`,
`validate`, `preview`, `doctor`, `schedule` and `mirror` look for
`faucet.yaml`, `faucet.yml`, `faucet.json` in the current directory.

## Cron: `faucet schedule`

```yaml
schedule:
  cron: "0 2 * * *"            # 5 fields, or 6 with leading seconds
  timezone: America/Los_Angeles  # IANA name, default UTC
  overlap_policy: skip         # skip | queue | forbid
  max_consecutive_failures: 5  # exit non-zero after N straight failures
  on_failure: continue         # continue | stop
  start_immediately: false
  run_timeout_secs: 3600       # per-run kill switch
  shutdown_grace_secs: 30
```

```bash
faucet schedule pipeline.yaml          # long-running; Ctrl-C / SIGTERM drains the in-flight run
faucet schedule pipeline.yaml --once   # one run now, then exit
```

`faucet run` ignores the `schedule:` block, and `faucet schedule` refuses a
config without one. `${now.*}` under `schedule` is the tick's scheduled time
in the schedule's timezone. Run it under a supervisor (systemd, a container
restart policy) so `max_consecutive_failures` exits get restarted. To run from
an external scheduler (cron, Kubernetes CronJob, an orchestrator) call
`faucet run` instead and omit `schedule:`.

## Historical replay: `faucet backfill`

```bash
faucet backfill pipeline.yaml --from 2026-06-01 --to 2026-07-01 --window 1d --concurrency 4 --dry-run
faucet backfill pipeline.yaml --from 2026-06-01 --to 2026-07-01 --window 1d --concurrency 4
faucet backfill pipeline.yaml --from 2026-06-01 --to 2026-07-01 --window 1d --resume
```

- Each window unit substitutes `${backfill.start}`, `${backfill.end}`,
  `${backfill.start_date}`, `${backfill.end_date}`, `${backfill.start_unix}`,
  `${backfill.end_unix}`, `${backfill.unit}` in source and sink configs, and
  sets the `${now.*}` clock to the window start. The source must use one of
  them, or every window reads the same data. `faucet run` refuses a config that
  still contains `${backfill.*}`, so use `${now.*}` when the same file must run
  both ways.
- Windows: `45s`, `30m`, `6h`, `1d`, `1w` (`1d` follows the calendar across
  DST; `24h` is elapsed time). Ranges are half-open `[from, to)`.
- Progress is recorded in the `state:` store; re-running the same range needs
  `--resume` or `--restart`. The live bookmark is never touched.
- Delivery is at-least-once per window: use `write_mode: upsert`, or
  `--into <sink-template>` to land in staging first.
- `--from-bookmark` / `--to-bookmark` / `--bookmark-field` replay between two
  bookmark values for non-time keys.
- Defaults can live in a `backfill:` block (`window`, `concurrency`, `timezone`).

## Long-running control plane: `faucet serve`

```bash
FAUCET_SERVE_AUTH_TOKEN=... faucet serve --listen 127.0.0.1:8080 --default-config pipeline.yaml
```

Default listen address is `127.0.0.1:8080`. Keep it on localhost (or behind a
reverse proxy with TLS) and keep auth on: `--auth-token` or the
`FAUCET_SERVE_AUTH_TOKEN` env var, or split `--read-token` / `--write-token` /
`--admin-token`. Never expose a `--no-auth` server. Configs submitted over HTTP
cannot use `extends` / `profiles` / `!include`.

## Health and limits

```yaml
sla:
  max_staleness_secs: 7200     # needs state:
  min_rows_per_run: 1          # catches a source silently returning nothing
  volume_anomaly: { method: zscore, sensitivity: 3.0, min_history: 5, window: 20 }
budget:
  max_records: 1000000         # a page that would cross it is refused whole
  max_duration_secs: 1800
observability:
  prometheus:
    listen: "127.0.0.1:9464"   # localhost unless a scraper needs more; never 0.0.0.0 by default
```

SLA violations never fail a run; they emit metrics and warnings and show in
`faucet doctor` / `faucet status`. A budget breach fails the run with the
bookmark unchanged. `observability.otel` pushes traces and metrics to an OTLP
collector (needs a build with the `otel` feature); `lineage:` emits
OpenLineage events (`namespace`, `transport: http|file|kafka`). Both never
fail a run.

## Exit codes worth scripting on

- `faucet validate`, `faucet run`: non-zero on any config error or failed row.
- `faucet doctor`: non-zero if any probe fails.
- `faucet test`: number of failed cases.
- `faucet status`: 0 healthy, 1 degraded or unknown, 2 failed.
- `faucet policy`: number of violations.
- `faucet backfill`: number of failed window units.
