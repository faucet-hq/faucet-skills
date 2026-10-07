# Observability

## Prometheus

### Pipelines (`run`, `schedule`, `mirror`, `backfill`)

Add an exporter to the config. The key is `listen`; `listen_addr` (which some
repository docs show) is rejected by `faucet validate`.

```yaml
observability:
  prometheus:
    listen: "127.0.0.1:9464"
```

The endpoint has no authentication. Bind it to `127.0.0.1` and scrape through
a local agent, or bind `0.0.0.0:9464` only inside a pod whose NetworkPolicy
admits just Prometheus. A port already in use is a startup error.

A `faucet run` process exits when the pipeline finishes, so a pull scrape
usually misses it. Metrics are dependable under `faucet schedule`, `faucet
serve` and long-running `faucet mirror`. For one-shot runs, alert on the exit
code (orchestrator or Job failure) and on `faucet status`.

### Serve

`faucet serve` serves `/metrics` on its own listen port, unauthenticated.
Restrict it at the network layer. The Helm chart's ServiceMonitor scrapes it.

### Metric names

Seen in a live scrape of 1.13.2 (`faucet schedule` with a file source and
JSONL sink), or present in the 1.13.2 source:

| Area | Metrics |
|---|---|
| Runs | `faucet_pipeline_runs_total{pipeline,row,source,sink,status}` (`ok` / `err`), `faucet_pipeline_run_duration_seconds`, `faucet_pipeline_invocation_duration_seconds`, `faucet_pipeline_in_flight`, `faucet_pipeline_start_time_unix_seconds` |
| Freshness | `faucet_pipeline_seconds_since_last_bookmark` (sources whose bookmark is a timestamp) |
| Source | `faucet_source_records_total`, `faucet_source_pages_total`, `faucet_source_bytes_total`, `faucet_source_page_duration_seconds`, `faucet_source_errors_total{kind}` |
| Lag (CDC / streams) | `faucet_source_lag_seconds`, `faucet_source_lag_bytes`, `faucet_source_lag_events` |
| Sink | `faucet_sink_records_total`, `faucet_sink_writes_total`, `faucet_sink_bytes_total`, `faucet_sink_write_duration_seconds`, `faucet_sink_flush_duration_seconds`, `faucet_sink_errors_total` |
| Batches / DLQ | `faucet_batch_outcomes_total{pipeline,row,sink,outcome}` (`committed`, `dlq_partial`, `dlq_all`, `failed`), `faucet_sink_dlq_records_total`, `faucet_sink_dlq_pages_total{reason}`, `faucet_sink_dlq_errors_total` |
| State | `faucet_state_errors_total{op,kind}` |
| Schedule | `faucet_schedule_runs_total{pipeline,outcome}`, `faucet_schedule_heartbeat_unix_seconds`, `faucet_schedule_consecutive_failures`, `faucet_schedule_next_tick_unix_seconds`, `faucet_schedule_run_lateness_seconds`, `faucet_schedule_last_run_duration_seconds`, `faucet_schedule_overlaps_total` |
| Serve | `faucet_serve_requests_total{method,path,status}`, `faucet_serve_request_duration_seconds`, `faucet_serve_runs_queued`, `faucet_serve_runs_in_flight`, `faucet_serve_runs_total`, `faucet_serve_history_degraded`, `faucet_serve_idempotency_hits_total`, `faucet_serve_cluster_instances`, `faucet_serve_runs_reclaimed_total`, `faucet_serve_triggers_fired_total`, `faucet_serve_trigger_healthy` |
| Build | `faucet_build_info{version}` (the 1.13.2 binary reports `1.13.1` here; do not use it to tell those two apart) |

Labels are `pipeline` (the config's `name:`), `row` and `connector`. `run_id`
is never a label. Keep `name:` stable or cardinality grows with every run.

Counters appear only after their first increment (for example
`faucet_schedule_overlaps_total`, the DLQ counters), so write alerts that
tolerate a missing series.

What each metric means when debugging: see the `faucet-debug` skill.

## Shipped dashboards and alert rules

The faucet-stream repository ships them under `observability/`:

| File | Content |
|---|---|
| `observability/grafana/faucet-pipeline-overview.json` | Runs by status, durations, throughput, errors, bookmark staleness |
| `observability/grafana/faucet-reliability.json` | Retries, circuit breaker, DLQ, quality, contracts, drift, throttling |
| `observability/grafana/faucet-schedule.json` | Scheduled runs, heartbeat, next tick, lateness, overlaps |
| `observability/grafana/faucet-serve.json` | HTTP, queue, history degraded, cluster, triggers |
| `observability/prometheus/alerts.yml` | Error-rate spike, no bookmark progress (1 h / 6 h), source lag, throttling, whole-batch DLQ, profile drift, circuit open, stuck scheduler, schedule lateness, consecutive failures, serve history degraded, OTLP and lineage drops |

Import the dashboards in Grafana (each has a data-source picker and a
`pipeline` variable) and load the rules with `rule_files:`.

## Minimum alerts

```yaml
groups:
  - name: faucet
    rules:
      - alert: FaucetRunsFailing
        expr: sum by (pipeline) (increase(faucet_pipeline_runs_total{status="err"}[30m])) > 0
        labels: { severity: warning }
      - alert: FaucetSchedulerStuck
        expr: time() - faucet_schedule_heartbeat_unix_seconds > 90
        labels: { severity: critical }
      - alert: FaucetScheduleFailingRepeatedly
        expr: faucet_schedule_consecutive_failures >= 3
        labels: { severity: critical }
      - alert: FaucetNoBookmarkProgress
        expr: faucet_pipeline_seconds_since_last_bookmark > 3600
        labels: { severity: warning }
      - alert: FaucetSourceLagHigh
        expr: max by (pipeline, row) (faucet_source_lag_seconds) > 3600
        labels: { severity: warning }
      - alert: FaucetRowsToDlq
        expr: sum by (pipeline, row) (increase(faucet_sink_dlq_records_total[1h])) > 0
        labels: { severity: warning }
      - alert: FaucetWholeBatchToDlq
        expr: increase(faucet_batch_outcomes_total{outcome=~"dlq_all|failed"}[1h]) > 0
        labels: { severity: critical }
      - alert: FaucetServeHistoryDegraded
        expr: faucet_serve_history_degraded == 1
        for: 5m
        labels: { severity: critical }
```

For CronJob-run pipelines, add a Job-failure alert from kube-state-metrics,
or run `faucet status` on a timer and alert on a non-zero exit.

## OpenTelemetry (OTLP)

**Not in the prebuilt binary or any published image.** A config with
`observability.otel` still passes `faucet validate`, but `faucet run` logs
`observability.otel is configured but this binary was built without
--features otel; OTLP export is disabled` and exports nothing (verified).
Build with `cargo install faucet-cli --features otel,schedule,serve,serve-ui,templates,lineage`
(or `--features full`), or a custom image with `otel` in `FEATURES`.

```yaml
observability:
  otel:
    endpoint: "http://otel-collector:4317"
    protocol: grpc          # grpc | http
    export: [traces, metrics]
    service_name: faucet
    sample_ratio: 0.1
```

Export is best-effort: a dead collector never fails a run. Watch
`faucet_otel_export_failures_total{signal}`. Prometheus and OTLP can run
together.

## Logs

Use `--log-format json` (or `FAUCET_LOG_FORMAT=json`) in every deployment.
Each line is one JSON object with `pipeline`, `row`, `run_id`, `connector` and
error `kind` as fields. Logs go to stderr. Keep `--log-level info` (or
`FAUCET_LOG=info`); never `debug` in production with resolved secrets, because
third-party connector debug output is outside faucet's redaction.
