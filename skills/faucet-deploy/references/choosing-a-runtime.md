# Choosing a runtime

faucet has three ways to run a pipeline. All three use the same config file
and the same state store, so you can move a pipeline between them without
touching its bookmark.

| Runtime | Process | Who decides when it runs | Use it when |
|---|---|---|---|
| `faucet run` | Starts, runs every row once, exits | Something outside faucet: cron, systemd timer, Airflow, Dagster, a Kubernetes CronJob or Job | You already have an orchestrator, the pipeline is one step in a DAG (load, then dbt), or you want the platform to own retries and history |
| `faucet schedule` | Long-running, fires on the config's `schedule.cron` | faucet itself | One host or one pod should run a pipeline on a timer with no orchestrator; you want warm auth tokens and connection pools between ticks |
| `faucet serve` | Long-running HTTP control plane | Callers over HTTP (`POST /v1/runs`), templates, the web console, or event triggers | Many pipelines, many callers, ad-hoc or API-driven runs, RBAC and an audit trail, or event-driven runs |

Rules of thumb:

- One pipeline on a timer, no orchestrator: `faucet schedule` under systemd
  or as a one-replica Deployment.
- A pipeline inside a larger DAG: `faucet run` from the orchestrator.
- Kubernetes owns the timer: a CronJob running `faucet run` (or
  `faucet schedule --once`). See [kubernetes-and-helm](kubernetes-and-helm.md).
- Several teams submit pipelines, or something must trigger runs over HTTP:
  `faucet serve`. See [serve](serve.md).
- Long-lived CDC or stream mirroring (`faucet mirror` with
  `mirror.continuous`) is its own long-running process; supervise it like
  `faucet schedule`.

## Never run two copies of one row at once

faucet writes a run lease (`{name}::{row}::__lease__`) while a row runs, but
in 1.13.2 the lease only blocks `faucet state set|reset|import`. It does
**not** stop a second `faucet run` of the same row. Two concurrent runs read
the same bookmark, both write, and race on the next bookmark. Make the
scheduler enforce one-at-a-time:

| Scheduler | Setting |
|---|---|
| `faucet schedule` | `overlap_policy: skip` (default), `queue` or `forbid`; never two schedule processes for one config |
| Kubernetes CronJob | `concurrencyPolicy: Forbid` |
| Airflow | `max_active_runs=1` on the DAG (and no parallel task on the same config) |
| Dagster | one run at a time for the asset or job (a concurrency limit or run queue tag) |
| cron | wrap with `flock -n /var/lock/<name>.lock faucet run …` |

## Orchestrators call `faucet run`

faucet is a single binary, so an orchestrator task is a shell command. Read the
exit code; read the summary from stdout:

```bash
faucet run /etc/faucet/orders.yaml --no-env-file --log-format json --output json > summary.json
```

- `--output json` prints one JSON document on **stdout** (per-row `status`,
  `error`, `rows_out`, `dlq_count`, `run_id`, totals). Logs stay on stderr.
  `--output ndjson` prints one object per matrix row.
- `--log-format json` (or `FAUCET_LOG_FORMAT=json`) makes stderr one JSON
  object per line with `pipeline`, `row`, `run_id`, `connector` fields.
- `--quiet` drops the live progress line (it is already off when stdout is not
  a terminal).
- `--no-env-file` stops faucet from loading a stray `.env` in the working
  directory. Inject variables from the platform instead.
- `--max-duration-secs`, `--max-records`, `--max-bytes` and
  `--allowed-sink` put a budget on one invocation.

Airflow (BashOperator) and Dagster (subprocess with `check=True`) work with no
plugin: a failed run exits non-zero and fails the task.

```python
extract_load = BashOperator(
    task_id="faucet_orders",
    bash_command="faucet run /opt/faucet/orders.yaml --no-env-file --log-format json",
)
```

### Exit codes

| Command | Exit code |
|---|---|
| `faucet run` | 0 when every row succeeded, non-zero when a row failed (verified: 1) |
| `faucet schedule` | 0 after a clean shutdown; non-zero on `on_failure: stop`, on reaching `max_consecutive_failures`, or on overlap with `overlap_policy: forbid` |
| `faucet validate` | non-zero when the config is invalid (with `--policy`: number of violations) |
| `faucet doctor` | number of failed probes (0 = all green) |
| `faucet status` | 0 healthy, 1 degraded or unknown, 2 failed |
| `faucet verify` | number of differing keys |
| `faucet policy` | number of violations |

### Retries are safe

The bookmark advances only after the sink confirms a page, so a retried run
resumes from the last confirmed page. Under the default at-least-once delivery
the page that was in flight when the run died can be written twice. If the
destination must not see that, use an upsert write mode with a key or
`delivery: exactly_once` (see the `faucet-pipelines` skill).

### Backfills

Do not loop `faucet run` yourself for history. Use `faucet backfill` (chunked,
resumable windows) or `faucet run --clock 2026-03-01` for a single dated
partition. See the `faucet-pipelines` skill.
