# Running a Singer tap or target inside faucet

Use the bridge when a tap has no native source and no hub template, or to
migrate one side at a time (keep the target, replace the tap, or the reverse).
It is a stepping stone, not the destination: plan to replace it with a native
`rest` / `graphql` config or a template later.

Tell the user, before choosing it:

- **Experimental.** `faucet list` labels the `singer` source "single-stream
  v0, Tier-2/experimental".
- **The Python runtime comes back.** faucet runs the tap as a child process;
  the tap and its interpreter must be installed on every machine that runs the
  pipeline (`pipx install <tap>` or a virtualenv). faucet does not install
  plugins.
- **Throughput is the tap's.** Records cross a process boundary as JSON lines.
- **One stream per config row.** RECORD messages for other streams are dropped.

## Source: run a tap

```bash
faucet init --source singer --discover --executable tap-inhouse-erp --stream invoices -o erp.yaml
faucet schema source singer
faucet validate --no-secrets erp.yaml
```

`init --discover` runs the tap's discovery, writes `catalog.json` next to the
output, and inlines a catalog in the config with the stream (and any parent
streams it can infer) marked `selected`. Without a selected stream most SDK
and database taps sync nothing and exit cleanly, which looks like success.
Parent-child taps may need the parent stream selected too; `init` warns when it
cannot tell.

| Key | Meaning |
|---|---|
| `executable` | tap on `PATH` or an absolute path (required) |
| `stream` | the one stream to emit (required) |
| `tap_config` | the tap's settings; written to a private temp file and passed as `--config`. Credentials go here as `${env:VAR}` |
| `catalog` | the Singer catalog object, passed as `--catalog` |
| `args` | extra arguments after faucet's own `--config` / `--catalog` / `--state` |
| `flush_on_state` | checkpoint at every STATE message (default `true`) |
| `idle_timeout_secs` | fail if the tap prints nothing for this long (default: wait forever); set it for unattended runs |
| `on_malformed` | `skip` (default) logs non-Singer lines; `fail` stops the run |

Converting a Meltano extractor: its `config` block (plus the settings Meltano
read from `.env`) becomes `tap_config`, with every secret replaced by
`${env:VAR}`; its `select` / `metadata` become the catalog's `selected` and
`replication-method` / `replication-key` metadata. Build the catalog with
`faucet init --discover` rather than translating patterns by hand. Several
streams from one tap means several matrix rows, each with its own `stream`
and the same `tap_config` / `catalog` (a `pipeline.sources` template with
`ref:` avoids repeating them). Each row runs the tap once.

Worked example: [examples/singer-bridge-to-postgres.yaml](../examples/singer-bridge-to-postgres.yaml).

### Resume granularity and duplicates

faucet saves the tap's STATE `value` as the row's bookmark, only after the
sink confirms every record before it, and hands it back to the tap as
`--state` on the next run. So:

- A crash replays from the tap's last STATE that was persisted. How much
  that is depends on how often the tap emits STATE.
- Many taps re-send rows at or after the bookmark value, and full-table taps
  re-send everything. **Pair the bridge with a keyed sink** (`write_mode:
  upsert` + `key`) so replays converge. With an append-only sink, expect
  duplicates.
- `ACTIVATE_VERSION` and `BATCH` messages are ignored by the source.

## Sink: keep a Singer target

```yaml
sink:
  type: singer
  config:
    target_command: target-jsonl          # on PATH or an absolute path
    target_config:                        # the loader's former meltano.yml config
      destination_path: ./out
    env: {}                               # extra env vars for the target process
    write_mode: upsert                    # sends key_properties = key
    key: [id]
    flush_on: exit                        # default; works with every target
```

- `target_config` is written to a 0600 temp file passed as `--config`; its
  string values are scrubbed from the target's stderr in faucet's logs.
- The Singer stream name is the matrix row id (or the pipeline `name` for a
  single-row config); `stream:` overrides it. Match the old stream name if the
  target derives table names from it.
- `flush_on: exit` closes the target at every flush and waits for a clean
  exit. `flush_on: state` keeps one target running and waits for it to echo
  STATE; use it only for targets known to echo STATE promptly.
- `write_mode: overwrite` sends `ACTIVATE_VERSION` after a successful run.
- A target exiting non-zero fails the run with its last stderr lines.

## Validate and test

`faucet validate --no-secrets` checks the config shape but not that the
executable exists or that the catalog selects anything. Before scheduling:

```bash
faucet doctor erp.yaml
faucet run erp.yaml --limit 100
faucet state show erp.yaml
```

A `--limit` run writes no bookmark. Confirm rows arrived, then run without
`--limit` and check that `faucet state show` reports a bookmark shaped like
the tap's STATE.
