# Governance: masking, quality, contracts, schema drift, policy

All of these are blocks inside `pipeline:` except `policy`, which is top level.
Order per page: transforms → masking → quality → contract → schema drift →
sink. The bookmark advances only after the sink confirms the page.

| Block | Fails the run? | Quarantines? | Needs `dlq:` |
|---|---|---|---|
| `masking` | Never | Never | No |
| `quality` | `on_failure: abort` | `quarantine`, `quarantine_batch` | When quarantining |
| `contract` | `on_breach: fail` (default) | `on_breach: quarantine` | When quarantining |
| `schema` | `on_drift: fail` | `on_drift: quarantine`, `on_incompatible: quarantine` | When quarantining |
| `policy` | Refuses the run before it starts; runtime `on_runtime: fail` | `on_runtime: quarantine` | When quarantining |

Quarantine is not allowed with the atomic-watermark exactly-once path (no DLQ there).

## Masking (PII)

Runs first, so PII never reaches a sink, the DLQ or lineage unmasked. Quality
and contract checks then see masked values: do not write a regex check against
a field you hash.

```yaml
masking:
  key: "${vault:secret/faucet#mask_key}"   # keyed HMAC for hash/tokenize; omit for plain SHA-256
  rules:                                   # first matching rule per field wins
    - name: emails
      match: { value_detector: email }      # email | credit_card | ssn | phone | ipv4
      action: { type: hash }
    - match: { field_pattern: '(?i)^ssn$' } # regex over the dot-path
      action: { type: redact }              # mask default "***"; `mask: null` nulls the field
    - match: { fields: [card_number] }      # explicit dot-paths
      action: { type: partial, keep_last: 4 }
      applies_to: [warehouse]               # sink template names or connector kinds; default all sinks
```

Actions: `redact`, `hash`, `tokenize` (`prefix`), `partial` (`keep_last`,
`mask_char`). `hash` / `tokenize` are deterministic, so masked values still join
across pipelines that share the `key`. Inspect with `faucet masking pipeline.yaml`.
`faucet schema masking` prints the schema.

## Quality checks

```yaml
quality:
  record:                                   # per record, first failure wins
    - { type: not_null, field: id, on_failure: abort }
    - { type: regex_match, field: email, pattern: '^[^@\s]+@[^@\s]+\.[^@\s]+$', on_failure: quarantine }
    - { type: value_in_set, field: status, values: [active, inactive], on_failure: quarantine }
    - { type: compare, field: amount, op: gte, value: 0, on_failure: quarantine }
  batch:                                    # per page, over survivors
    - { type: row_count, min: 1, on_failure: abort }
    - { type: unique, fields: [id], on_failure: quarantine }
    - { type: null_rate, field: email, max: 0.05, on_failure: quarantine_batch }
```

Record checks: `not_null`, `not_empty`, `regex_match`, `value_in_set`,
`not_in_set`, `compare` (`gt|gte|lt|lte|eq|ne`), `type_is`, `string_length`
(`json_schema` only in builds with `quality-jsonschema`). Batch checks:
`row_count`, `null_rate`, `unique`, `distinct_count`. Record checks and
`unique` take `quarantine` or `abort`; `row_count`, `null_rate`,
`distinct_count` take `quarantine_batch` or `abort`. Use `abort` for "upstream
is broken" signals (null primary key, empty page), `quarantine` for bad
individual rows.

## Data contract

A versioned promise about output shape, checked after quality.

```yaml
contract:
  version: "1.0.0"                # required; semver recommended
  owner: data-platform
  on_breach: quarantine           # fail (default) | quarantine | warn
  allow_extra_fields: true        # false: an undeclared top-level key is a breach
  fields:
    - { name: order_id, type: string, min_length: 1 }
    - { name: status, type: string, enum: [open, shipped, cancelled] }
    - { name: amount, type: number, min: 0 }
    - { name: email, type: string, required: false, nullable: true }
```

Field types: `string`, `integer`, `number`, `boolean`, `object`, `array`.
Fields default to `required: true`, `nullable: false`. Constraints: `enum`,
`pattern`, `min`/`max`, `min_length`/`max_length`.

```bash
faucet contract pipeline.yaml                         # validate + summary
faucet contract pipeline.yaml --export json-schema    # or: contract, openlineage
```

## Schema drift

Compares each page's top-level shape with the live destination schema.

```yaml
schema:
  on_drift: evolve              # warn (default) | ignore | fail | quarantine | evolve
  allow_type_widening: true     # evolve only
  on_incompatible: fail         # evolve only: fail | quarantine
```

- `evolve` applies additive / widening DDL and needs an evolution-capable
  sink: `postgres`, `mysql`, `mssql`, `sqlite`, `bigquery`, `elasticsearch`
  (add fields only). Some connectors add more in newer builds; on any other
  sink validate rejects `evolve` and prints the accepted list.
- Schemaless sinks (`file`, `s3`, `gcs`, `kafka`, `mongodb`, `redis`, `http`,
  `snowflake`, `dynamodb`, …) make every policy inert.
- `evolve` is refused with `write_mode: overwrite`.
- Detection is top-level only: a nested object is one column.

## Data-flow policy

Labels columns, then states which sinks a label may reach. Evaluated before any
data moves (`validate`, `plan`, `doctor`, `faucet policy`); `faucet run`
refuses a violating config.

```yaml
policy:
  classifications:
    - { label: pii, fields: [email, phone], value_detector: email }
  rules:
    - name: pii-eu
      when: { label: pii }
      require: { residency: [eu] }       # sink attribute must match ...
      mask: [hash, tokenize]             # ... or the column arrives masked
pipeline:
  sinks:
    warehouse:
      type: postgres
      attributes: { residency: eu, environment: prod }
      config: { … }
```

Every classification needs `fields`, `field_pattern` or `value_detector`;
every rule needs `when.label` and one of `require`, `mask`, `deny: true`.

```bash
faucet policy pipeline.yaml                       # exit code = violations
faucet validate pipeline.yaml --policy org-policy.yaml   # merge a central policy file
```
