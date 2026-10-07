# Hardening

faucet holds credentials for every system it touches and runs with the full
trust of its process. Harden the process, the network around it and, for
`faucet serve`, who may submit configs.

## Network and TLS

- `faucet serve` speaks plain HTTP. Terminate TLS at an ingress, a load
  balancer or a sidecar; never expose port 8080 directly.
- The default serve bind is `127.0.0.1:8080`. The container images set
  `FAUCET_SERVE_LISTEN=0.0.0.0:8080`, which is right inside a pod and wrong on a
  host network.
- `/metrics` (serve and the pipeline exporter), `/healthz` and `/readyz` have no
  auth. Keep them on loopback or behind a NetworkPolicy.
- Egress: a submitted or triggered config can call any address the pod can
  reach, cloud metadata included. Restrict egress to the systems your
  pipelines use. `--callback-allow-host` restricts per-run callbacks.
- Connector TLS is per connector and some default to plaintext (for example
  `postgres-cdc` `tls: disable`). Set TLS explicitly on every production
  connection, the state store URL and the history URL included
  (`sslmode=require` or stricter, `rediss://`).

## Authentication on serve

- Never `--no-auth` beyond `127.0.0.1`. 1.13.2 does not warn when you do.
- The 1.13.2 images default to `serve --no-auth`; always set the command.
- Prefer RBAC (`--auth-config` or the read/write/admin token trio) over one
  admin token. Default to `viewer`; give `operator` only to orchestrators.
- In 1.13.2, `--auth-config` tokens are literal; `${env:…}` is not expanded
  and would become a guessable token. Generate tokens (`openssl rand -hex 32`),
  keep the file in a Secret, rotate by replacing the Secret and restarting.
- Pass tokens through env vars or files, never as flags.
- Turn on `--require-approval run` where a second person must approve ad-hoc
  runs.
- Keep `--preview-local-outputs` off; it serves file contents over HTTP.
- In 1.13.2 a submitted config may use the `singer` source or sink, which runs
  a program on the host. Treat `operator` as host access.

## Secrets

- Never inline credentials. Use `${env:VAR}` from a platform secret, or
  `${file:/path}` for file-shaped secrets mounted read-only.
- `${vault:…}`, `${aws-sm:…}`, `${gcp-sm:…}`, `${azure-kv:…}` need a
  `secrets-*` build; the prebuilt binary and the images reject them at load
  time (`faucet validate --no-secrets` still passes, so CI will not catch it;
  `faucet validate` without the flag does).
- Pass `--no-env-file` in deployments so a stray `.env` is never read.
- Faucet redacts resolved secrets only in its own logs. Third-party driver
  debug output, metric labels and span attributes are not covered.

## Logs and levels

- `--log-level info` (or `warn`); `--log-format json`.
- Never run with `FAUCET_LOG=debug` or a verbose third-party filter while a
  config holds resolved secrets.

## Process and filesystem

- Run as non-root (the images use uid 65532), read-only root filesystem, all
  capabilities dropped, seccomp `RuntimeDefault`, no service-account token.
  The Helm chart does all of this by default.
- Writable paths: `/tmp` and only the state / output directories you need.
- The DLQ holds raw failed records. Put it somewhere with the same access
  controls as the source data, and configure `masking:` if the source has PII.
- Bookmarks can contain key values: restrict access to the state store.

## Supply chain and versions

- Pin `faucet --version` in CI and images to one release; pin image tags to
  `<version>-<profile>`, never `latest`.
- Before an upgrade: `faucet state export … -o backup.json` and
  `faucet migrate --state … --check`.

## Checklist

- [ ] TLS set on every connector, state store and history database
- [ ] Credentials only via Secret-backed `${env:…}` / `${file:…}`; `--no-env-file` set
- [ ] Log level `info` or quieter, JSON logs
- [ ] `masking:` configured where the source has PII; DLQ location access-controlled
- [ ] serve: RBAC, generated tokens, no `--no-auth`, TLS at the ingress, egress restricted
- [ ] serve: postgres history so audit and runs survive restarts
- [ ] `/metrics` reachable only by Prometheus
- [ ] Containers non-root, read-only root, capabilities dropped
- [ ] Image and binary versions pinned; state exported before upgrades
