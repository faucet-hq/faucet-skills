# faucet-sink-acme

Example faucet sink: writes records to an acme collection through a bulk endpoint, as appends or as keyed upserts.

```yaml
sink:
  type: acme
  config:
    base_url: https://acme.example.com/api
    token: ${env:ACME_TOKEN}
    collection: orders
    write_mode: upsert
    key: [id]
```

`cargo test` runs the unit tests and the `faucet-conformance` battery against a `wiremock` backend.
