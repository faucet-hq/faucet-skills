# Transforms

`pipeline.transforms` is an ordered list of `{ type, config }` steps applied
to every record between source and sink. `faucet list` prints the transforms
in this build; `faucet schema transform <type>` prints each one's config.
Invalid transform config (a bad enum value, a missing required field) is
caught by `faucet validate`.

## Catalogue

| Type | Does | Key config |
|---|---|---|
| `flatten` | Nested objects to flat keys | `separator` (default `__`) |
| `keys_case` | Re-case every key | `mode`: snake, camel, pascal, kebab, screaming_snake; `on_collision` |
| `rename_keys` | Regex rename of every key | `pattern`, `replacement` |
| `rename_field` | Exact rename | `fields: { old: new }` |
| `select` / `drop` | Keep / remove top-level fields | `fields: [..]` |
| `set` | Add or overwrite constants | `values: { k: v }` (may use `${now.*}`) |
| `cast` | Coerce types | `fields: { name: int|float|bool|string|timestamp }`, `on_error` |
| `redact` | Replace values with a mask | `fields`, `mask` |
| `hash` | SHA-256 / BLAKE3 tokens | `fields`, `algorithm`, `encoding`, `salt`, `into` |
| `value_case` | lower / upper / trim string values | `fields`, `mode` |
| `json_parse` / `json_encode` | String to JSON and back | `fields` |
| `coalesce` | Fill missing/null | `field`, `default` or `from: [..]` |
| `split` / `join` | String to array and back | `field`, `delimiter` |
| `filter` | Keep records matching a predicate | `path`, `op`: eq, ne, exists, in, not_in; `value` |
| `explode` | One record per array element | `path`, `prefix`, `on_missing`, `carry` |
| `unpivot` | Wide columns or a map to long rows | `id_fields`, `key_name`, `value_name`, `columns` or `from` |
| `lookup` | Join an inline / JSONL reference table | `values` or `jsonl`, `on: { record, ref }`, `add` |
| `zip_columns`, `tree_flatten`, `cross_join` | Report-shaped payloads to rows | see schema |
| `cdc_unwrap` | CDC envelope to flat row + `__op` marker | defaults fit the built-in CDC sources |

`sql` (DuckDB over a page) and `wasm` exist only in builds with the
`transform-sql` / `transform-wasm` features; check `faucet list`.

Field-targeting transforms (`select`, `drop`, `set`, `rename_field`, `cast`,
`redact`, `value_case`) act on top-level fields only. Run `flatten` first to
reach nested values. Missing fields are skipped, never created as `null`.

## Ordering rules

1. Unwrap and reshape first: `cdc_unwrap`, `json_parse`, `explode`, `flatten`.
2. Normalise names next: `keys_case`, `rename_field`. Later steps use the new names.
3. Trim with `select` / `drop`, then `filter`.
4. Fix values: `cast`, `value_case`, `coalesce`, `lookup`.
5. Stamp last: `set` (so a later step does not overwrite the stamp).

Masking, quality, contract and schema-drift passes run after the whole
transform chain, so they see the final field names. Write quality and
contract rules against post-transform names.

```yaml
transforms:
  - type: flatten
    config: { separator: "_" }
  - type: keys_case
    config: { mode: snake }
  - type: select
    config: { fields: [id, email, status, address_country, updated_at] }
  - type: filter
    config: { path: status, op: ne, value: deleted }
  - type: cast
    config: { fields: { id: string }, on_error: error }
  - type: set
    config: { values: { ingested_on: "${now.date}" } }
```

## Layers in matrix configs

Final chain per row = `pipeline.transforms` ++ source template `transforms` ++
row `transforms`. Set `inherit_transforms: false` on a source template or a
row to drop the layers above it. Sinks reject `transforms`.

## Testing transforms offline (`faucet test`)

A spec feeds fixture records through the real transform, quality and contract
path with an in-memory source, sink and DLQ. Nothing external is contacted and
secrets-manager references are not resolved.

```yaml
# tests/customers.yaml
version: 1
tests:
  - name: clean customer passes and is reshaped
    config: ../pipeline.yaml
    input:
      - { id: 1, email: "Ada@Example.com", name: Ada, status: active, address: { country: UK }, updated_at: "2026-01-01T00:00:00Z" }
    expect:
      records:
        - { id: 1, email: "ada@example.com", name: Ada, status: active, address_country: UK, updated_at: "2026-01-01T00:00:00Z" }
  - name: bad status is quarantined
    config: ../pipeline.yaml
    input: [ { id: 2, email: "b@example.com", status: deleted } ]
    expect: { records_written: 0, dlq_count: 1 }
  - name: null id aborts
    config: ../pipeline.yaml
    input: [ { id: null, email: "c@example.com", status: active } ]
    expect: { error: "id" }
```

```bash
faucet test tests/*.yaml            # exit code = number of failed cases
faucet test tests/*.yaml --json
faucet test tests/*.yaml --filter quarantined
```

Case fields: `name`, `config` (path, relative to the spec) or inline
`pipeline: { transforms, quality, contract }`, `row` (matrix row id), `input`
(inline list or a `.jsonl` / `.json` / `.yaml` file), `page_size`, `clock`
(pin it when transforms use `${now.*}`). Expectations: `records`, `dlq`,
`records_written`, `dlq_count`, `error` (substring), `unordered: true`,
`match: subset`. State, delivery and schema drift do not apply in tests.
`faucet schema test` prints the spec schema.
