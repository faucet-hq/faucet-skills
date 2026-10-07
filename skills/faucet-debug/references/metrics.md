# Metrics

## Turning metrics on

Prometheus is configured in the pipeline config. The listen address is
logged at startup (`Prometheus /metrics listening on …`):

```yaml
observability:
  prometheus:
    listen: "0.0.0.0:9090"
```

OTLP push (traces and metrics) is an `otel:` sub-block next to it. It needs a
binary built with the `otel` feature (included in the full build):

```yaml
observability:
  otel:
    endpoint: "http://localhost:4317"
    protocol: grpc            # grpc | http
    export: [traces, metrics]
```

Both can run at once. Export is best-effort: a dead collector never fails or
slows a run. It increments `faucet_otel_export_failures_total{signal}`
instead.

Common labels: `pipeline`, `row` (matrix row id; empty for single-row
configs), `connector`. `run_id` is a span and log field only, never a label.

A one-shot `faucet run` exits when it finishes, so a scrape can miss it. For
one-shot runs, use `faucet run --output json` and the logs. Metrics are most
useful under `faucet schedule` and `faucet serve`.

## Which metric answers which question

### Which row is slow?

| Metric | Labels | Use |
|---|---|---|
| `faucet_pipeline_invocation_duration_seconds` | `pipeline,row,source,sink,status` (`ok`/`err`) | Wall-clock per matrix row. Compare `status="ok"` series by `row` |
| `faucet_pipeline_run_duration_seconds` | `pipeline,row,source,sink` | Duration of the core pipeline run |
| `faucet_source_page_duration_seconds` | `pipeline,row,connector` | Time to fetch one page |
| `faucet_sink_write_duration_seconds`, `faucet_sink_flush_duration_seconds` | `pipeline,row,connector` | Time to write / flush one batch |
| `faucet_source_roundtrips_total{op}`, `faucet_sink_roundtrips_total{op}` | plus `*_roundtrip_duration_seconds` | Calls actually made to the backend, retries included |
| `faucet_pipeline_adaptive_batch_size` | `pipeline,row` | Current batch size under adaptive batching |

```promql
histogram_quantile(0.95, sum by (row, le) (rate(faucet_pipeline_invocation_duration_seconds_bucket{status="ok"}[1h])))
```

If page duration dominates, the source is the bottleneck: raise source
concurrency (`--concurrency`, `request_concurrency`, `max_connections`) or
check throttling. If write duration dominates, raise the sink's `batch_size`,
or use a bulk / `COPY` path where the sink offers one.

### Is it rate-limited?

| Metric | Labels | Use |
|---|---|---|
| `faucet_source_throttled_total` | `pipeline,row,connector` | Rate-limit responses received (`429`, `RateLimited`) |
| `faucet_source_throttle_wait_seconds` | `pipeline,row,connector` | Time actually slept after each |
| `faucet_source_retries_total` | `pipeline,row,connector,class` | Source retries by `rate_limited`, `http_5xx`, `connection`, `timeout` |
| `faucet_sink_throttled_total`, `faucet_sink_throttle_wait_seconds`, `faucet_sink_retries_total` | same | Sink-side mirror |

```promql
sum by (row) (rate(faucet_source_throttle_wait_seconds_sum[15m])) / 900
```

A value near 1 means the row spends most of its time waiting on the quota.
Only `rest`, `graphql` and `xml` sources report through these metrics today.

### Is it behind?

| Metric | Labels | Use |
|---|---|---|
| `faucet_source_lag_bytes` | `pipeline,row,connector` | Unread WAL / binlog (`postgres-cdc`, `mysql-cdc`) |
| `faucet_source_lag_events` | same | Unconsumed messages (`kafka`, `mssql-cdc`) |
| `faucet_source_lag_seconds` | same | Age of the oldest unread change (`mongodb-cdc`, `mssql-cdc`, `oracle-cdc`, `kinesis`, `dynamodb` streams) |
| `faucet_pipeline_seconds_since_last_bookmark` | `pipeline,row` | Time since the last committed bookmark |
| `faucet_pipeline_last_bookmark_unix_seconds` | `pipeline,row` | When the last bookmark was committed |

Lag gauges are polled at page boundaries (at most every 15 s) and at run
end. Between scheduled runs they are stale. Use `faucet status --probe` for a
fresh reading.

### Why did it fail?

| Metric | Labels | Use |
|---|---|---|
| `faucet_pipeline_runs_total` | `pipeline,row,source,sink,status,kind` | `kind` = error kind on `status="err"` |
| `faucet_source_errors_total`, `faucet_sink_errors_total` | `…,kind` | Errors by kind per side |
| `faucet_transform_errors_total` | `kind` | Transform failures |
| `faucet_state_errors_total` | `op,kind` | State-store failures |
| `faucet_resilience_retries_total` | `pipeline,row,op,class` | Sink-side retries (`op` = `sink_write`, `flush`, `state_put`) |
| `faucet_resilience_giveup_total` | `pipeline,row,op` | Retries exhausted |
| `faucet_resilience_circuit_state`, `faucet_resilience_circuit_opened_total` | `pipeline,row` | Circuit breaker open (1) / opened |
| `faucet_resilience_poison_rows_total` | `pipeline,row,action` | Rows given up on per row |

### Where did rows go?

| Metric | Use |
|---|---|
| `faucet_source_records_total` vs `faucet_sink_records_total` | Read vs written |
| `faucet_transform_records_in_total` / `faucet_transform_records_out_total` | Ratio below 1 means filtering, above 1 means fan-out |
| `faucet_source_replication_key_missing_total` | Records without their `replication_key` (misspelled or nested key) |
| `faucet_batch_outcomes_total{outcome}` | Per write: `committed`, `dlq_partial`, `dlq_all`, `failed` |
| `faucet_sink_dlq_records_total` | Rows dead-lettered |
| `faucet_quality_records_quarantined_total` | Rows quarantined by quality checks |
| `faucet_contract_violations_total` | Contract breaches |
| `faucet_schema_drift_total` | Drifted columns, labelled by `mode` and drift kind |
| `faucet_pipeline_pages_skipped_total` | Exactly-once pages skipped because the sink already committed them |

### Scheduler

| Metric | Labels | Use |
|---|---|---|
| `faucet_schedule_overlaps_total` | `pipeline,policy` | Ticks that hit a still-running run |
| `faucet_schedule_runs_total` | `pipeline,outcome` | Scheduled runs by outcome |
| `faucet_schedule_consecutive_failures` | `pipeline` | Current failure streak |
| `faucet_schedule_last_run_duration_seconds` | `pipeline` | Compare with the cron period |
| `faucet_schedule_run_lateness_seconds` | `pipeline` | Start delay versus the tick |
| `faucet_schedule_heartbeat_unix_seconds`, `faucet_schedule_next_tick_unix_seconds` | `pipeline` | Is the scheduler alive |

### SLA, profiling, verify

`faucet_pipeline_sla_violations_total{kind}` (`staleness`, `min_rows`,
`volume`, `lag`), `faucet_profile_drift_total{column,metric}`,
`faucet_verify_differences_total{kind}`. See
[profiling and SLA](profiling-and-sla.md).

`faucet_build_info{version}` is always `1`; join on it to see which release
produced a series.
