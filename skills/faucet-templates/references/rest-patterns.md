# REST and GraphQL patterns for source templates

These are the `rest` connector options a template most often needs. They go in
`source.config` (shared) or a stream's `source.config` (merged on top). The full
list, including `async_job`, `odata`, `records_multi`, `decode` and `discovery`,
is in `faucet schema source rest`. Look up anything not covered here before you
use it.

## Request basics

| Field | Default | Notes |
|---|---|---|
| `base_url` | | API root. Make it a param defaulting to the public URL. |
| `path` | | Per stream. `{key}` placeholders are filled from partitions. |
| `method` | `GET` | |
| `headers` | | Static headers on every request (`Accept`, an API-version header). Auth headers win on a clash. |
| `query_params` | | Map of **strings**. A whole-value `${param.x}` of `type: int` becomes a number and is rejected. |
| `query_params_multi` | | Repeated keys: `{ "fields[]": [a, b] }`. |
| `body` | | JSON body (POST search endpoints). |
| `records_path` | whole body | JSONPath to the record array, e.g. `$.data[*]`, `$.workflow_runs[*]`. |
| `drop_key_prefixes` | `[]` | Drop protocol fields such as `_links` or `@odata.`. |

## Auth (inline)

| `type` | `config` fields |
|---|---|
| `bearer` | `token` |
| `basic` | `username`, `password` |
| `api_key` | `header`, `value` |
| `api_key_query` | `param`, `value` |
| `oauth2` | `token_url`, `client_id`, `client_secret`, `scopes` (client-credentials flow, token cached and refreshed) |

```yaml
auth: { type: api_key, config: { header: X-API-Key, value: "${param.api_key}" } }
```

Other inline types: `none`, `token_endpoint` (`url`, `method`, `body`,
`encoding: json|form`, `token_path`, `expiry_path`) and `custom` (a `headers`
map). For a token shared across connectors, or a refresh-token flow, use the
template's top-level `auth:` catalog and `auth: { ref: <name> }` (see
[source-template-anatomy.md](source-template-anatomy.md#shared-auth-catalog)).
Every secret field is a `secret: true` param.

## Pagination

| `type` | Fields | Stops when |
|---|---|---|
| `None` | | after the first page (single-object endpoints) |
| `Cursor` | `next_token_path`, `param_name` | the token is null or absent, or repeats |
| `CursorInBody` | `next_token_path`, `body_cursor_field` | as `Cursor`, with the cursor written into the JSON body (POST search) |
| `LinkHeader` | | no `rel="next"` in the `Link` header |
| `NextLinkInBody` | `next_link_path` | the next URL is absent, empty or repeats |
| `PageNumber` | `param_name`, `start_page`, `page_size`, `page_size_param` | an empty page, or the same body twice |
| `Offset` | `offset_param`, `limit_param`, `limit`, `total_path` | an empty or short page, or offset reaches `total` |
| `OffsetInBody` | `offset_field`, `limit_field`, `limit`, `stop_when_short` (and `rows_path` / `total_path`) | a short page, or offset reaches `total` |
| `RecordFieldCursor` | `field`, `into`, `param`, `agg`, `page_size`, `stop_when_short` | a short page (keyset paging on a record field) |

```yaml
pagination: { type: Cursor, next_token_path: $.next_cursor, param_name: cursor }
pagination: { type: LinkHeader }
pagination: { type: NextLinkInBody, next_link_path: $.links.next }
pagination: { type: CursorInBody, next_token_path: $.paging.next.after, body_cursor_field: after }
```

A single-object stream under a paginated shared source overrides it with
`pagination: { type: None }`. With `LinkHeader` / `NextLinkInBody`, static
`query_params` and query binds are sent on the first request only, because the
next link already carries them. `max_pages` caps pages per pass and is unset by
default.

`persist_cursor: true` on a `Cursor` / `CursorInBody` stream saves the final
cursor as the bookmark and resumes from it on the next run. Use it for
`/sync`-style feeds.

## Incremental streams

```yaml
streams:
  - name: invoices
    source:
      config:
        path: /invoices
        replication_method: { type: Incremental }
        replication_key: updated_at                  # field name as the API returns it
        start_replication_value: "${param.api_start_date}"
        replication_bind: { into: query, name: updated_since, format: iso8601 }
    primary_keys: [id]
    write: [upsert, append]
```

- `replication_key` is a sibling of `replication_method`. It is a top-level
  name, a dot path (`commit.committer.date`), or a JSON Pointer. It is **not**
  a JSONPath.
- `replication_key` is read from the **raw API record**, before the template's
  transforms. If the API returns `updatedAt` and `keys_case` renames it to
  `updated_at`, the key is `updatedAt`. A wrong key does not fail the run, but
  the bookmark never advances. With a state store, the second run should send
  the newer value. Check that it does.
- `start_replication_value` is the first-run lower bound. Make it a param.
- Without `replication_bind`, filtering happens client-side after download.
  `replication_bind` pushes the bookmark into the request:
  `into: query|header|body|path`, `name` (or `path`, a JSON Pointer for
  `into: body`), `template` (default `${bookmark}`, e.g. `"gte|${bookmark}"`),
  `format: raw|iso8601|epoch_s|epoch_ms|date`, `value_type: string|number`, and
  `advance_from` (a JSONPath into the response to advance from).
- `on_missing_key: keep|drop|fail` decides what happens to a record without the
  key. The default is `keep`.
- Bookmarks persist only when the run has a `state:` store, which comes from a
  deployment overlay. Incremental streams usually use `write: [upsert, append]`.

### Windowed (bounded-range) APIs

For report APIs that need both bounds or cap the span, use a `window` (it needs
`Incremental` + `replication_key` + a start value):

```yaml
replication_method: { type: Incremental }
replication_key: date
start_replication_value: "${param.start_date}"
window:
  step: 30d
  lookback: 3d
  lower: { into: query, name: start_date, format: date }
  upper: { into: query, name: end_date, format: date }
```

`granularity` makes inclusive ranges non-overlapping, and `max_windows` caps a
sweep (default 10000). A template can use `${window.start}` and
`${window.end}` in one string. Each completed window's end is saved as the
bookmark.

## Rate limits and retries

| Field | Default | Notes |
|---|---|---|
| `timeout` | 30 | Seconds per request. |
| `max_retries` | 3 | Transient failures and 429s. |
| `retry_backoff` | 1 | Base seconds. Exponential with jitter, capped at 60s. A 429's `Retry-After` is honoured. |
| `request_delay` | | Seconds between page requests. Use it for APIs with a strict requests-per-second limit. |
| `retry_on_response` | `[]` | Treat other responses as throttling. |
| `tolerated_http_errors` | `[]` | Statuses treated as an empty page on the **first** request only. |

`retry_on_response` matchers take `status`, `header`, `body_path` + `values`,
`match_success`, `backoff_secs`, `backoff_from`, and `max_wait_secs`:

```yaml
max_retries: 5
retry_on_response:
  - { status: [403, 429], header: retry-after }
  - { status: [403, 429], header: x-ratelimit-remaining, values: ["0"], backoff_secs: 60 }
  - { status: [400], body_path: "$.error.code", values: [17, 32], backoff_secs: 60 }
```

## GraphQL sources

`type: graphql` takes `endpoint`, `query`, `variables`, `records_path`,
`batch_size` (the page size sent as `first`), and Relay-style pagination:

```yaml
source:
  type: graphql
  config:
    endpoint: "${param.api_base_url}/graphql"
    auth: { type: bearer, config: { token: "${param.api_token}" } }
streams:
  - name: orders
    source:
      config:
        query: "query Orders($first: Int, $after: String, $query: String) { orders(first: $first, after: $after, query: $query) { nodes { id updatedAt } pageInfo { hasNextPage endCursor } } }"
        records_path: "$.data.orders.nodes[*]"
        pagination: { has_next_page_path: "$.data.orders.pageInfo.hasNextPage", cursor_path: "$.data.orders.pageInfo.endCursor", cursor_variable: after, page_size_variable: first }
        batch_size: 100
        replication_method: { type: Incremental }
        replication_key: updatedAt
        start_replication_value: "${param.api_start_date}"
        replication_bind: { variable: query, template: "updated_at:>'${bookmark}'", format: iso8601 }
    primary_keys: [id]
    write: [upsert, append]
```

Offset pagination is `pagination: { type: Offset, offset_variable, page_size, stop_when_short }`.
GraphQL `retry_on_response` can match a 200 whose body reports throttling
(`match_success: true`, `body_path: "$.errors[*].extensions.code"`). Schema:
`faucet schema source graphql`. The hub's `faucet-hq/shopify` is a complete
GraphQL template.
