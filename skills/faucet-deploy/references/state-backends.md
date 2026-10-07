# State backends

The state store holds each row's bookmark (the resume position), the
exactly-once watermark, the run-status and lease markers, SLA and profiling
history. If the store forgets, the next run starts from scratch (duplicates
downstream); if two writers share one key without coordination, the bookmark
races. Choose the backend from where the process runs.

All four are compiled into the prebuilt binary and every published image
(`faucet list` ends with `State stores: memory, file, redis, postgres`).
The block lives under `pipeline:`:

```yaml
  state:
    type: postgres
    config:
      url: ${env:FAUCET_STATE_PG_URL}
      table: faucet_state        # default
      ensure_table: true         # CREATE TABLE IF NOT EXISTS; false if migrations own it
      max_connections: 5         # default
```

```yaml
  state:
    type: redis
    config:
      url: ${env:FAUCET_STATE_REDIS_URL}   # rediss:// for TLS
      namespace: prod-orders               # [A-Za-z0-9_.-], no ':'
```

```yaml
  state:
    type: file
    config:
      path: /var/lib/faucet/state
```

## Which one is safe where

| Where it runs | memory | file | redis | postgres |
|---|---|---|---|---|
| Laptop, tests, `--dry-run` | yes | yes | yes | yes |
| One VM / systemd unit, local disk | no | yes | yes | yes |
| Container with an `emptyDir` or no volume | no | **no** (lost each pod) | yes | yes |
| Kubernetes CronJob / Job | no | only with a ReadWriteOnce PVC and `concurrencyPolicy: Forbid` | yes | yes |
| `faucet schedule` as a Deployment | no | only with a PVC, `replicas: 1`, `strategy: Recreate` | yes | yes |
| `faucet serve` with several replicas or `--cluster` | no | **no** (each pod has its own disk) | yes | yes |
| Pods on different nodes sharing one pipeline | no | no | yes | yes |

Rules:

- **memory** never persists. Production configs must not use it.
- **file** writes one JSON file per key, atomically. It is correct for a single
  host with a disk that outlives the process. The Helm chart's Job and CronJob
  mount an `emptyDir`, so `file` state there is thrown away after every run;
  use redis or postgres with the chart.
- **redis** is a shared network store (one `SET` per bookmark). Use a Redis
  with persistence (AOF or RDB) turned on, or a restart loses bookmarks. Give
  each environment its own `namespace`.
- **postgres** is the durable, transactional default for production, and the
  natural choice when the sink is already postgres. It can live in the same
  server as the data in its own table. Back it up like any other table.

Two processes must never run the same row at the same time, whatever the
backend: the run lease does not block a second run (see
[choosing-a-runtime](choosing-a-runtime.md)).

Use the same `name:` and the same state block in every environment that is
meant to share progress, and different ones (or a different redis
`namespace` / postgres `table`) for environments that must not.

## Check it at deploy time

```bash
faucet doctor /etc/faucet/orders.yaml          # includes a put/get/delete probe on the state store
faucet status /etc/faucet/orders.yaml          # bookmark, last success / failure per row
```

## Back up and move state

Export before every upgrade, backend change or risky edit:

```bash
faucet state export /etc/faucet/orders.yaml -o orders-state-$(date +%F).json
faucet migrate --state /etc/faucet/orders.yaml --check
```

Move a pipeline from `file` to `postgres` without losing its position:

```bash
faucet state export orders.yaml -o orders-state.json
faucet state import orders.yaml orders-state.json --to-state "$FAUCET_STATE_PG_URL" --dry-run
faucet state import orders.yaml orders-state.json --to-state "$FAUCET_STATE_PG_URL" --yes
```

Then change the config's `state:` block to postgres. `import` refuses while a
run holds a row's lease; stop the scheduler first. Bookmark edits
(`state set`, `state reset`) are covered by the `faucet-debug` skill.

## At rest

Bookmarks can contain key values. Redis and postgres inherit the security of
those servers: TLS (`rediss://`, `sslmode=require` or stricter in the URL),
a dedicated user with access to only the state table or key prefix. The `file`
backend can encrypt bookmark files (`config.encryption.key`), but only in a
build with the `encryption` feature, which neither the prebuilt binary nor the
published images include.
