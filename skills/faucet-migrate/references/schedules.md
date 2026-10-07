# Schedules and jobs

## Where the schedule should live

| Today | After the migration |
|---|---|
| `meltano schedule` entries run by Meltano's scheduler or `meltano invoke airflow` | a `schedule:` block run by `faucet schedule`, under a supervisor (systemd, a container restart policy) |
| Airflow / Dagster / Prefect / cron calls `meltano run ...` | keep the orchestrator; replace the call with `faucet run pipeline.yaml` (no `schedule:` block needed) |
| an Airbyte connection schedule | `schedule:` + `faucet schedule`, or an external scheduler calling `faucet run` |
| a job that runs dbt after the load | the orchestrator runs `faucet run` then dbt; faucet does not run dbt |

If something downstream depends on ordering (dbt after the load, one load
after another), keep that in the orchestrator or use matrix `depends_on`
inside one faucet config. A `schedule:` block does not chain separate files.

## Translating the interval

`faucet schedule` takes a five-field cron plus an IANA `timezone`. Meltano
intervals:

| Meltano `interval` | `schedule.cron` |
|---|---|
| `@hourly` | `"0 * * * *"` |
| `@daily` | `"0 0 * * *"` |
| `@weekly` | `"0 0 * * 0"` |
| `@monthly` | `"0 0 1 * *"` |
| `@yearly` | `"0 0 1 1 *"` |
| a cron string | the same string |
| `@once` | no schedule; run `faucet run` once |

Check which timezone the old scheduler evaluated cron in (Meltano hands its
schedules to an orchestrator such as Airflow, which defaults to UTC) and set
`timezone` explicitly to keep the same wall-clock times.

```yaml
schedule:
  cron: "15 1 * * *"
  timezone: UTC
  overlap_policy: skip           # do not start a run while the previous one is still going
  max_consecutive_failures: 5    # exit so the supervisor notices
```

Other keys (`run_timeout_secs`, `start_immediately`, `on_failure`,
`max_runs`, `shutdown_grace_secs`): `faucet schema schedule`.

```bash
faucet validate --no-secrets pipeline.yaml       # prints "schedule: cron ... — valid"
faucet schedule pipeline.yaml --once             # one run now, then exit
faucet schedule pipeline.yaml                    # long-running
```

`faucet run` ignores the `schedule:` block; `faucet schedule` refuses a config
without one. `${now.*}` under `faucet schedule` renders as the tick's
scheduled time, so a missed-and-retried tick still reads its own window.

## Jobs

A Meltano job is a list of tasks run in order. Split it:

- each `tap [mappers] target` task → one faucet config (or one matrix row per
  stream in a shared config);
- `dbt:run`, `dbt:test` and utilities → the orchestrator, after `faucet run`
  exits 0;
- several extract-loads in one job that must run in order → `depends_on`
  between matrix rows, or separate orchestrator tasks.

`faucet run` exits non-zero when any row fails; with `execution.on_error:
continue` the other rows still run first. Use `faucet run --output json` in
an orchestrator to read per-row results.

## During the parallel run

Run faucet on its own schedule next to the old one, offset by a few minutes
if both read the same database, and into its own dataset (see
[cutover.md](cutover.md)). Disable the old schedule only at cutover.
