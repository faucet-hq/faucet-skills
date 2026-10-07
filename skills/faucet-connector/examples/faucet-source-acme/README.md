# faucet-source-acme

Example faucet source: reads an acme collection over HTTP with keyset pagination (`?after=<id>&limit=<n>`) and resumes from the last committed id.

```yaml
source:
  type: acme
  config:
    base_url: https://acme.example.com/api
    token: ${env:ACME_TOKEN}
    collection: orders
```

`cargo test` runs the unit tests and the `faucet-conformance` battery against a `wiremock` backend.
