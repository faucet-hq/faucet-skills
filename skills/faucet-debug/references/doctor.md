# faucet doctor

`faucet doctor` probes every connector in a config (auth, network,
permissions, reachability) and prints a green/red checklist. It writes no
data. Run it first when a run fails to start or fails on its first request.

```bash
faucet doctor pipeline.yaml
faucet doctor pipeline.yaml --timeout-secs 5     # fail fast against dead hosts (default 10)
faucet doctor pipeline.yaml --json               # { config, invocations, summary }
faucet doctor pipeline.yaml --offline            # static lints only, no network, no credentials
faucet doctor pipeline.yaml --profile prod       # probe a `profiles:` overlay
faucet doctor pipeline.yaml --env-file .env.prod
```

Exit code = number of failed probes (clamped to 255). `0` means every probe
passed or was skipped. `--offline` exits non-zero only on lint errors
(dangling or unused `auth:` providers, unused `vars:`, `batch_size: 0` no-ops),
not on warnings.

## Reading the checklist

```text
▸ Invocation default::eu-west  (source=postgres, sink=bigquery)
  ✓ source [postgres] read                                      39 ms
  ✗ sink   [bigquery] auth (dataset eu_west not found)         410 ms
        hint: check bigquery credentials and that the dataset exists
```

- `✓` pass. `✗` fail: the text in brackets is the reason, and `hint:` says
  what to check. `•` skip: not applicable (a connector without a probe, a CDC
  slot not created yet, a path that cannot be cheaply checked).
- One block per matrix invocation. Child invocations (parent/child matrices)
  are listed but not probed, because they need parent records that exist only
  at run time.

## What each probe does

| Role | Probe |
|---|---|
| Most sources | Pulls one page through the real read path (DNS, TLS, auth, first request), then stops |
| `postgres-cdc` | Replication slot reachable (a missing slot is `skip`: `run` can create it) |
| `kafka` source / sink | Cluster metadata request |
| SQL sinks | `SELECT 1` on the pool |
| Object-store sinks | Bucket head or metadata list |
| Warehouse sinks | Token mint plus a read-only metadata call |
| File sinks | Target directory is writable |
| State store | Sentinel put / get / delete that leaves nothing behind |
| `sla:` block | Staleness and volume-baseline checks against stored run history |
| Sources with a head (CDC, streams) | `lag` from the stored bookmark; fails past a `max_lag_*` threshold |

## Mapping failures to causes

- **Source `read` fails**: wrong credentials, host unreachable, TLS problem,
  or the first request is rejected. Re-run with `--log-level debug` for the
  full HTTP status and URL.
- **Sink `auth` / `io` fails**: missing dataset, table, bucket or directory,
  or the account lacks a grant.
- **`state` fails**: the state backend is down or the credentials are wrong.
  Do not run until this passes. A run that cannot save its bookmark repeats
  work on every attempt.
- **`sla` staleness fails**: no successful run within `max_staleness_secs`.
  Go to `faucet status` to see the last error.
- **`lag` fails**: the source is further behind than a `max_lag_*` threshold.

## Related offline checks

```bash
faucet validate pipeline.yaml --no-secrets       # grammar and structure only
faucet validate pipeline.yaml --show-composed    # composed config before interpolation
faucet explain pipeline.yaml                     # what the config does, in plain English
```

`doctor` resolves secrets like `run` does. Probe reasons are scrubbed of
resolved secrets, but avoid sharing debug-level logs from a config with live
secrets.
