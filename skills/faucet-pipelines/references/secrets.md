# Secrets

Credentials never appear literally in a config. Every password, token, key,
and URL that embeds credentials is a reference resolved at load time.

| Form | Source | Needs |
|---|---|---|
| `${env:VAR}` | Environment variable (or a sibling `.env` file) | Always available |
| `${secret:VAR}` | Same as `env`; use it to mark a value as a secret for readers | Always available |
| `${file:PATH}` | File contents (mounted secret) | Always available |
| `${vault:<path>[#field]}` | HashiCorp Vault KV v2 | `secrets-vault` build feature; `VAULT_ADDR`, `VAULT_TOKEN` |
| `${aws-sm:<name-or-arn>[#field]}` | AWS Secrets Manager | `secrets-aws-sm`; default AWS credential chain |
| `${gcp-sm:projects/<p>/secrets/<s>/versions/<v>}` | GCP Secret Manager | `secrets-gcp-sm`; application default credentials |
| `${azure-kv:<vault>/<secret>[/<version>]}` | Azure Key Vault | `secrets-azure-kv`; Azure default credential chain |

**Check which backends your binary has.** The prebuilt binary (Homebrew or the
installer script) is built without the secrets-manager features, so `${vault:…}`,
`${aws-sm:…}`, `${gcp-sm:…}` and `${azure-kv:…}` fail at run time with "built
without the `secrets-…` feature". With the prebuilt binary, put credentials in
environment variables (filled from your secret store by your scheduler or CI) and
reference them with `${env:VAR}`. To resolve secrets-manager references directly,
install a build that includes them:

```bash
cargo install faucet-cli --features secrets
```

`#field` (Vault and AWS) parses the secret as JSON and takes one key:

```yaml
connection_url: "postgresql://app:${aws-sm:prod/db#password}@${aws-sm:prod/db#host}/mydb"
```

## Where references work

Everywhere config interpolation runs: connector configs, `state:`, `dlq:`,
matrix rows, `mirror.snapshot.source`, the top-level `auth:` catalog, `vars:`,
`masking.key`, notification channels. Prefer the shared `auth:` catalog when
several connectors use one credential:

```yaml
auth:
  api:
    type: static
    config:
      token: "${vault:secret/data/app#token}"
pipeline:
  source:
    type: rest
    config: { base_url: "https://api.example.com", auth: { ref: api } }
```

Order: `${env:…}` / `${file:…}` / `${vars.…}` resolve first, secrets-manager
directives last, so a path may be built from env: `${vault:secret/data/${env:APP_ENV}/api#token}`.

## Validating without credentials

```bash
faucet validate --no-secrets pipeline.yaml
```

- Skips all secrets-manager lookups (`vault`, `aws-sm`, `gcp-sm`, `azure-kv`),
  even when the binary lacks that backend.
- Still resolves `${env:…}` / `${secret:…}` / `${file:…}`: a missing variable
  is an error. Export placeholders or keep a non-secret `.env` for CI
  (`--env-file PATH` picks another file; `--no-env-file` disables it).
- Without `--no-secrets`, validate fetches every reference and prints one
  `→ resolved` line per reference (never the value). That is a real
  credential preflight; `faucet doctor` then checks the connections.
- `faucet explain`, `faucet plan` (unless `--resolve-secrets`) and `faucet test`
  do not resolve secrets-manager references.
- `faucet schema secrets` prints the directive grammar.

## Redaction limits

faucet scrubs resolved secret values (4+ characters) from its own logs and
errors. It cannot scrub what a connector library logs on its own, metric
labels, or span attributes. So:

- Do not run with `FAUCET_LOG=debug` / `RUST_LOG=debug` on configs that hold secrets.
- Do not put secrets in fields that become labels or file paths (table names,
  `name`, `state_key`, sink paths).
- Do not commit `.env` files that hold real values.
