# `faucet schedule`

A long-running foreground process that runs one pipeline config on a cron
schedule. In the prebuilt binary and in every published image. Stop it with
SIGTERM or Ctrl-C.

```bash
faucet schedule /etc/faucet/orders.yaml --no-env-file --log-format json
faucet schedule /etc/faucet/orders.yaml --once      # one run now, then exit
```

The config needs a top-level `schedule:` block. Without one, `faucet schedule`
refuses and tells you to use `faucet run`. `faucet run` ignores the block, so
one file works for both. Full example:
[scheduled-pipeline.yaml](../examples/scheduled-pipeline.yaml).

## The `schedule:` block

| Key | Default | Meaning |
|---|---|---|
| `cron` | required | 5 fields (`min hour dom mon dow`) or 6 with leading seconds. `@daily`-style strings are not accepted |
| `timezone` | `UTC` | IANA name. DST-correct: a repeated hour fires once, a skipped time fires at the next valid instant |
| `overlap_policy` | `skip` | What a tick does while the previous run is still going: `skip` (drop it), `queue` (buffer one, in memory only), `forbid` (exit non-zero) |
| `on_failure` | `continue` | `continue` waits for the next tick; `stop` exits non-zero on the first failed run |
| `max_consecutive_failures` | none | Exit non-zero after N failed runs in a row, so the supervisor restarts or pages. A success resets the count |
| `run_timeout_secs` | none | Abort a run that exceeds this; it counts as a failure |
| `shutdown_grace_secs` | `30` | On SIGTERM/SIGINT, wait this long for the in-flight run |
| `start_immediately` | `false` | Run once at startup before the first tick |
| `max_runs` | none | Exit cleanly after this many successful runs |

`faucet validate` prints `schedule: cron '…' tz '…' — valid` when the block
parses. Check it in CI.

Production defaults: `overlap_policy: skip`, `on_failure: continue`,
`max_consecutive_failures: 5` (or what your paging budget allows),
`run_timeout_secs` a little under the cron period, and
`shutdown_grace_secs` longer than your slowest page flush.

## Missed ticks

The scheduler advances from the scheduled tick, not the wall clock. A tick
that fires a little late still runs. If the process was down for many
periods, the backlog collapses into one catch-up run. There is no burst of
backfilled runs. To fill a historical gap use `faucet backfill` or
`faucet run --clock <date>`.

`${now.*}` tokens resolve to the tick's scheduled time in `timezone`, so a
restarted tick writes to the same dated path.

## Signals and graceful drain

| Signal | Effect |
|---|---|
| SIGTERM / SIGINT | Stop taking ticks, wait up to `shutdown_grace_secs` for the in-flight run, exit 0 if it finished. Otherwise abort it; the next start resumes from the last confirmed bookmark (the in-flight page is re-read) |
| SIGHUP | Re-read and re-validate the config, swap it in for the next tick. An invalid file is rejected and the old config keeps running. Changes to the top-level `auth:` catalog, lineage and notifier need a restart |

In Kubernetes set `terminationGracePeriodSeconds` above
`shutdown_grace_secs`, or the kubelet kills the run mid-page.

## Supervising it

systemd restarts on a non-zero exit, which is what
`max_consecutive_failures` and `on_failure: stop` produce:

```ini
[Service]
Type=simple
ExecStart=/usr/local/bin/faucet schedule /etc/faucet/orders.yaml --no-env-file
EnvironmentFile=/etc/faucet/orders.env
Restart=on-failure
RestartSec=30s
User=faucet
```

In Kubernetes, run `faucet schedule` as a Deployment with `replicas: 1` and
`strategy: Recreate` (a rolling update briefly runs two schedulers on one
config). If you would rather Kubernetes own the timer, use a CronJob with
`faucet run` instead; see [kubernetes-and-helm](kubernetes-and-helm.md).

Use a durable state backend. A `file` store only works when the same disk
comes back after a restart; see [state-backends](state-backends.md).

## Metrics to alert on

Add `observability.prometheus.listen` to the config. Verified names in 1.13.2:

| Metric | Alert when |
|---|---|
| `faucet_schedule_heartbeat_unix_seconds{pipeline}` | `time() - value > 90`: loop stuck or process gone |
| `faucet_schedule_consecutive_failures{pipeline}` | `>= 3` (or your threshold) |
| `faucet_schedule_runs_total{pipeline,outcome}` | `outcome="err"` increasing |
| `faucet_schedule_overlaps_total{pipeline,policy}` | increasing: runs take longer than the period (appears after the first overlap) |
| `faucet_schedule_run_lateness_seconds` | p95 above a minute |
| `faucet_schedule_next_tick_unix_seconds` | `value - time()` far above the period |
| `faucet_schedule_last_run_duration_seconds` | approaching the cron period |

See [observability](observability.md) for the shipped alert rules.
