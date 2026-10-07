# Streaming pages and batch sizing

## The page

```rust
pub struct StreamPage {
    pub records: Vec<Value>,      // written to the sink as one batch
    pub bookmark: Option<Value>,  // Some => flush the sink, then persist this
}
```

The pipeline pulls one page, writes it, and only then polls the next. If the page carries `Some(bookmark)` it calls `sink.flush()` and then `StateStore::put(state_key, bookmark)` before polling again. Memory is O(page size) on both sides, regardless of how big the dataset is. Pages are consumed strictly in order, one at a time.

## Native streaming

The default `stream_pages` calls `fetch_with_context_incremental`, which loads the whole result into a `Vec` and then chunks it. That is correct but unbounded. Any source with a paging primitive must override `stream_pages` and yield as it reads. The pattern, using the re-exported `async_stream`:

```rust
fn stream_pages<'a>(
    &'a self,
    _context: &'a HashMap<String, Value>,
    batch_size: usize,
) -> Pin<Box<dyn Stream<Item = Result<StreamPage, FaucetError>> + Send + 'a>> {
    let page_size = self.config.page_size.unwrap_or(batch_size);
    let single_page = page_size == 0;
    let limit = if single_page { MAX_API_LIMIT } else { page_size.min(MAX_API_LIMIT) };
    Box::pin(async_stream::try_stream! {
        let mut cursor = self.start_cursor()?;
        let mut all = Vec::new();
        loop {
            let records = self.fetch_page(cursor.as_ref(), limit).await?;
            let exhausted = records.len() < limit;
            if let Some(last) = records.last() {
                cursor = Some(cursor_of(last, &self.config.cursor_field)?);
            }
            if single_page {
                all.extend(records);
            } else if !records.is_empty() {
                yield StreamPage { records, bookmark: cursor.as_ref().map(bookmark) };
            }
            if exhausted { break; }
        }
        if single_page && !all.is_empty() {
            yield StreamPage { records: all, bookmark: cursor.as_ref().map(bookmark) };
        }
    })
}
```

Inside `try_stream!`, `?` on a `FaucetError` ends the stream with that error. For a database cursor the shape is the same: open the cursor once, push rows into a buffer, `yield` and `std::mem::replace` the buffer each time it reaches the page size, yield the remainder at the end.

Things to get right:

- **Lock scope.** Never hold a `std::sync::MutexGuard` across an `.await` inside the stream; it makes the stream `!Send`. Clone what you need out of the lock in a separate sync helper.
- **End of data.** Decide termination from the backend's own signal (short page, null next-cursor, end of cursor), not from an empty page alone, and make sure an empty first page ends cleanly.
- **Unchanged order.** Never reorder or run page fetches concurrently in a way that could emit page N+1 before page N. Bookmark order must match emit order.
- **Error mid-stream.** Return the error. Pages already checkpointed stay durable and the next run resumes from them.
- **Missing cursor value.** A record without the cursor field is an error (`FaucetError::Source`), never a silent skip; skipping would advance the bookmark past data.

## Where the bookmark goes

- **Whole-result sources** (a query whose high-water mark is only known at the end) attach the bookmark to the final page only.
- **Ordered, deterministic sources** (keyset pagination on a unique increasing key, CDC) may attach a bookmark to every page. Each page then becomes a checkpoint, so a crash replays at most one page. The cost is a sink flush per page.
- A source that knows its bookmark but read no records may yield one empty page carrying it, so the checkpoint still advances.
- The bookmark is opaque to everyone but your source. Keep it a small JSON object (`{"after": 1234}`), never a whole record.

## Batch size

- `batch_size` passed to `stream_pages` is a hint from the pipeline (default `faucet_core::DEFAULT_BATCH_SIZE` = 1000, max `MAX_BATCH_SIZE` = 1,000,000). If your config has its own page-size field, the config value wins when set; otherwise use the hint.
- `batch_size == 0` means "no batching": emit the whole result as one page. Handle it explicitly. It is for small lookup tables and sinks that prefer one large request, not a shortcut around streaming.
- Clamp to the backend's maximum request size. Yielding pages smaller than the hint is fine; larger is not.
- Conformance check 2 (`assert_bounded_memory(&source, batch, total)`) asserts the largest page is `<= batch` and smaller than `total`, so build the source under test without a config page size larger than `batch`.

## Sink-side sizing

A sink receives whatever page size the source emitted. If the destination has a request limit (rows, bytes), split inside `write_batch`:

```rust
for chunk in records.chunks(self.config.max_request_records) {
    written += self.post(chunk).await?;
}
```

Expose the limit as a config field with a safe default. Remember that splitting one page into several requests makes a failed `write_batch` partially committed, so `batch_atomicity()` must stay `BestEffort` unless the whole page commits in one transaction.

## Buffering sinks

If the sink buffers (file writer, multipart upload, client-side batch), `flush()` is where data becomes durable. The pipeline calls it before every checkpoint and on every exit path, including cancellation. A sink that only commits on `flush` must make each `flush` a complete, readable commit, and must tolerate `flush` being called with nothing buffered.
