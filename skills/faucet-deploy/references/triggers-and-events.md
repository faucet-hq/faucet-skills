# Event triggers (`faucet serve --triggers`)

**Not in the prebuilt binary.** `faucet serve --triggers t.yaml` there fails
with `--triggers requires a build with the triggers feature`, and
`faucet schema triggers` does not exist (both verified on 1.13.2). Use:

| Trigger type | Feature | Published image |
|---|---|---|
| `webhook` | `triggers` | every profile (`-core`, `-analytics`, `-cdc`, `-full`); verified on `1.13.2-core` |
| `schedule` | `triggers` + `schedule` | every profile |
| `object_arrival` (S3, GCS) | `triggers-object-store` | `-full` only |
| `queue_depth` (redis list/stream, kafka topic) | `triggers-redis` / `triggers-kafka` | `-full` only |

Or build: `cargo install faucet-cli --features serve,serve-ui,schedule,templates,serve-history-postgres,triggers,triggers-object-store`.

A trigger enqueues an ordinary run, so it goes through the same queue,
idempotency, history, RBAC and audit as `POST /v1/runs`. Run triggers on a
server with persistent history (postgres) so their runs and dedupe keys survive
a restart.

## File shape

```yaml
# triggers.yaml (paths in `config:` are relative to this file)
version: 1
triggers:
  - name: sync-hook
    type: webhook
    config: ./pipelines/sync.yaml
    methods: [POST]                 # default [POST]
    dedupe_header: Idempotency-Key  # header value becomes the idempotency key
    debounce_secs: 0

  - name: load-dropped-files
    type: object_arrival
    config: ./pipelines/s3_load.yaml
    store: { type: s3, bucket: my-bucket, prefix: incoming/, region: us-east-1 }
    poll_interval_secs: 30          # default 30
    mode: per_object                # or batch
    start_at: now                   # or beginning
    run:
      name: "load:{name}:{object_key}"
      timeout_secs: 3600

  - name: drain-queue
    type: queue_depth
    config: ./pipelines/drain.yaml
    queue: { type: redis, url: "redis://redis:6379", key: jobs, kind: list }
    threshold: 100                  # default 1
    poll_interval_secs: 30
```

Each trigger takes either `config:` (a path or an inline pipeline document) or
`template:` (`{id, version, params}`) for a registered template, plus
`enabled: false` to park it. Other `store` types: `gcs` (`bucket`, `prefix`).
Other `queue` types: `kafka` (`brokers`, `topic`, `group`).

The pipeline config reads event data through `${trigger.*}`:
`${trigger.name}`, `${trigger.fired_at}`, and per type
`${trigger.object_key}` / `${trigger.bucket}` / `${trigger.size}` /
`${trigger.last_modified}` (object arrival, `per_object`),
`${trigger.object_count}` (`batch`), `${trigger.body}` /
`${trigger.header.<name>}` / `${trigger.query.<name>}` / `${trigger.method}`
(webhook), `${trigger.queue}` / `${trigger.depth}` (queue depth).

## Firing a webhook

Webhook triggers are `POST /v1/triggers/{name}` on the serve API and need a
token with the `operator` role:

```bash
curl -s -XPOST http://faucet:8080/v1/triggers/sync-hook \
  -H "Authorization: Bearer $FAUCET_TRIGGER_TOKEN" \
  -H 'Idempotency-Key: orders-2026-10-07' -H 'content-type: application/json' -d '{}'
```

A repeat with the same `Idempotency-Key` returns the same `run_id` (verified).
The dedupe value is trusted as sent: only use `dedupe_header` for trusted
callers. Treat `${trigger.body}` and headers as untrusted input; never splice
them into SQL or paths without validation.

## Watching triggers

`/readyz` lists each trigger with `healthy`, `consecutive_failures`,
`last_error`, `last_fire`. Metrics: `faucet_serve_trigger_healthy`,
`faucet_serve_triggers_fired_total`, `faucet_serve_trigger_errors_total`,
`faucet_serve_trigger_runs_dropped_total`. The shipped `faucet-serve` Grafana
dashboard has a trigger panel.
