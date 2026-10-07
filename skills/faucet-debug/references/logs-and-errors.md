# Logs and errors

## Log flags

Every subcommand accepts the two global flags:

| Flag | Env | Values |
|---|---|---|
| `--log-level` | `FAUCET_LOG` | `error`, `warn`, `info` (default), `debug`, `trace`, or a filter directive such as `faucet_core=debug,info` |
| `--log-format` | `FAUCET_LOG_FORMAT` | `text` (default) or `json` (one object per line on stderr) |

Precedence for the level: `--log-level`, then `FAUCET_LOG`, then `RUST_LOG`,
then `observability.tracing.level` in the config.

```bash
faucet run pipeline.yaml --log-level debug
faucet run pipeline.yaml --log-format json --output json > summary.json 2> run.log
faucet run pipeline.yaml --select orders --log-level debug --quiet
FAUCET_LOG=debug faucet run pipeline.yaml
```

With `--log-format json`, the span fields `pipeline`, `row`, `run_id`,
`connector` and error `kind` are real JSON fields, so you can filter with
`jq 'select(.row=="orders" and .level=="ERROR")'`. Under `json` the
end-of-run human table is not printed. The same numbers arrive as
`pipeline completed` / `row completed` events.

Resolved secrets are scrubbed from log output in both formats. Third-party
connector logging at `debug` is outside that boundary, so do not paste debug
logs from a config with live secrets.

## The run summary

`faucet run --output json` writes one document to stdout (logs stay on
stderr). `--output ndjson` writes one object per row.

| Field | Use |
|---|---|
| `status`, `totals.failed` | Did the run succeed |
| `rows[].status`, `rows[].error` | Which row failed and why |
| `rows[].duration_ms` | Slowest row |
| `rows[].rows_in`, `rows[].rows_out` | Read vs written (`rows_in` is null unless a `lineage:` or `catalog:` block is present) |
| `rows[].dlq_count`, `rows[].batches` | Dead-lettered rows and per-write outcomes |
| `rows[].bookmark` | Bookmark after the run |
| `rows[].source_lag` | Source lag at the end of the run |
| `rows[].run_id` | The id `faucet rollback --run` takes |

A failed run exits `1` with `N pipeline invocation(s) failed (see logs above
for details)`. The per-row cause is in the log and in `rows[].error`.

## Error kinds

Runtime errors are typed. The kind appears in logs (`kind` field), in
`faucet status` (`last error: <Kind>: …`), on the
`faucet_pipeline_runs_total{status="err",kind=…}` counter, and in DLQ
envelopes (`error.kind`).

| Kind | Meaning | Retried automatically | First move |
|---|---|---|---|
| `Http` | Transport failure: DNS, connect, TLS, timeout | yes (connection errors and 5xx) | `faucet doctor` |
| `HttpStatus` | Non-success HTTP status, with URL and truncated body | 5xx and 429 yes, other 4xx no | Read the body. 401/403 means credentials or grants |
| `RateLimited` | 429 or rate-limit signal; retries ran out | yes | Throttling recipe |
| `Auth` | Credential or token flow failed | no | `faucet doctor`, check secrets |
| `Config` | Invalid config or validation | no | `faucet validate pipeline.yaml` |
| `Url` | URL could not be built | no | Check `base_url` / path templating |
| `Json`, `JsonPath` | Response not parseable, or a JSONPath failed | no | `faucet preview pipeline.yaml --limit 5` |
| `Transform` | A transform could not compile or apply | no | `faucet plan pipeline.yaml --live` |
| `Source` | Source-side operation failed (query, file read) | no | Message names the operation |
| `Sink` | Sink-side write failed | only on sinks with idempotent writes | Message names the write |
| `QualityFailure` | A quality check failed under an `abort` policy | no | Fix data or relax the check |
| `ContractViolation` | A record breached the data contract under `on_breach: fail` | no | `faucet contract pipeline.yaml` |
| `SchemaDrift` | Page shape diverged from the destination under `on_drift: fail` | no | Schema-drift recipe |
| `ProfileDrift` | Column profile drifted under `profiling.on_drift: fail`. Data was already written | no | `faucet profiling show` |
| `PolicyViolation` | A labelled column would reach a sink the policy forbids | no | `faucet policy pipeline.yaml` |
| `BudgetExceeded` | `max_records`, `max_bytes` or `max_duration_secs` crossed. The page that would cross is refused before writing, so the bookmark never passes it | no | Raise the budget or narrow the run |
| `State` | A state-store read, write or delete failed | state puts are retried when a `resilience:` block is set | `faucet doctor` (state probe) |
| `StateIncompatible` | Stored bookmark written by a newer faucet or another source. Refused before reading the source | no | [state](state.md) |
| `CircuitOpen` | `resilience.circuit_breaker` saw too many consecutive fully-failed pages | no | Fix the sink. `schedule` waits `cooldown_secs` before the next tick |
| `Custom` | Error from a third-party connector | depends | Read the message |
| `Panic` | A connector panicked. Isolated, reported as a metric kind | no | Report a bug with the log |

"Retried automatically": transient classes (`http_5xx`, `rate_limited`,
`connection`, `timeout`) are retried by the source connector, and on the sink
side by a `resilience:` block. A plain sink write is retried only when the
sink commits idempotently (`postgres`, `mysql`, `mssql`, `sqlite`, `iceberg`,
`bigquery`, `kafka`), because retrying a write whose response was lost would
duplicate rows.

## Config-time errors

Errors before any connector is built come from config loading:
`missing environment variable 'X'`, `unknown source 'foo'. Available: …`
(the binary was built without that connector; check `faucet list`),
`matrix has a parent cycle`, `duplicate matrix row id`. Run:

```bash
faucet validate pipeline.yaml
faucet validate pipeline.yaml --json
faucet list
```
