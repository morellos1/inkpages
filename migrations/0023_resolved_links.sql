-- Cross-run cache for shortener resolution (t.co, bit.ly, ...): a short URL's
-- destination is effectively immutable, so resolve it once instead of
-- re-HEADing every shortener in the corpus on every pipeline run.
-- Failures are not cached (retried, throttled, next run).
create table resolved_links (
    short_url   text primary key,
    final_url   text not null,
    resolved_at timestamptz not null default now()
);
