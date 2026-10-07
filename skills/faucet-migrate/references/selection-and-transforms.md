# Selection, stream maps and mappers → faucet

Meltano decides what reaches the target in three places: `select` (streams
and columns), `metadata` (replication), and mappers / stream maps (row-level
changes). faucet does the same work in the source query or config, in
`transforms:`, and in `masking:`. Check every transform's keys with
`faucet schema transform <name>`; the list of transforms in this build is in
`faucet list`.

## `select` → what the source reads

| Meltano | faucet |
|---|---|
| `select: [public-orders.*]` (stream on, all columns) | a matrix row (or config) for that table / endpoint; SQL sources: `SELECT *` or an explicit column list |
| `select: [public-orders.id, public-orders.status]` | SQL sources: list the columns in the query (cheapest: unread columns never leave the database). API sources: `select` transform `{ fields: [id, status] }` |
| `"!public-customers.password_hash"` | SQL: leave the column out of the query. API: `drop` transform `{ fields: [password_hash] }` |
| a stream not selected | no row for it |
| `select_filter` / `--select` at run time | `faucet run --select <row>` / `--only` / `--skip` / `--tag` |

Column selection in the SQL itself is preferred over `select` / `drop`
transforms: it moves less data and keeps secrets out of the pipeline
entirely.

## Stream maps → transforms

| Stream-map construct | faucet |
|---|---|
| `"col": __NULL__` (remove a property) | `drop: { fields: [col] }` |
| `"new": "old"` alias (copy a property under a new name) and removing the old | `rename_field: { fields: { old: new } }` |
| regex-style renames across many keys | `rename_keys: { pattern, replacement }` or `keys_case: { mode: snake }` |
| `"col": "md5(col)"` / hashing for joins | `hash: { fields: [col], into: col_hash }` (SHA-256 or BLAKE3; `salt` from `${env:…}`). md5 is not offered, so hashes will not match old values; rebuild joins or exclude the column from verification |
| `"col": "'***'"` (constant mask) | `redact: { fields: [col] }` or a `masking:` rule |
| `"col": "str(col)"`, `int(...)`, type coercion | `cast: { fields: { col: int } }` |
| a constant column (`"source_system": "'erp'"`) | `set: { values: { source_system: erp } }` |
| `"col": "col or other"` (fallback) | `coalesce: { field: col, from: [other] }` |
| lower / upper / trim | `value_case` |
| `__filter__: "status == 'paid'"` | `filter: { path: status, op: eq, value: paid }` (`ne`, `exists`, `in`, `not_in` too). For SQL sources, a `WHERE` clause is better |
| flattening nested objects (`flattening_enabled`, `flattening_max_depth` on SDK taps) | `flatten: { separator: "__" }` (SDK taps flatten with `__`) |
| JSON strings ↔ objects | `json_parse`, `json_encode` |
| one record per array element | `explode` |
| `__alias__` (rename the stream) | the destination table name in the sink config |
| `__source__` / splitting one stream into several | one matrix row per output, each with its own `filter` |
| arbitrary Python expressions, `datetime` math, cross-field arithmetic | **no equivalent** in the prebuilt binary. The `sql` transform needs a source build with that feature (faucet-hq/faucet-stream#820); otherwise move the logic downstream (dbt) |

Transforms run per page, in order, after the source and before masking,
quality checks and the sink. Put `drop` / `select` early so later steps see
fewer fields. Test non-trivial chains offline with `faucet test` (see the
`faucet-pipelines` skill).

## PII handling

If a mapper existed to hide or hash PII, prefer a `masking:` block over
ad-hoc transforms: rules match by field name, regex or value detector, apply
before any sink sees a row, and can be scoped per sink. `faucet masking
pipeline.yaml` prints which rules hit which sinks. Details are in the
`faucet-pipelines` skill (governance reference).

## Singer metadata columns

`target-*` loaders with `add_record_metadata` write `_sdc_extracted_at`,
`_sdc_batched_at`, `_sdc_deleted_at` and others. faucet's `metadata_columns:`
block stamps `extracted_at`, `loaded_at`, `run_id`, `source` and `sequence`
under a configurable prefix. With `prefix: _sdc`, the columns are named
`_sdc_extracted_at`, `_sdc_loaded_at`, and so on, which keeps models that only
read `_sdc_extracted_at` working. There is no `_sdc_batched_at` or
`_sdc_deleted_at`; tell the user which downstream references will break.
`faucet verify` ignores `_faucet_*` by default; add the `_sdc_*` pattern to
`verify.exclude` when using that prefix.
