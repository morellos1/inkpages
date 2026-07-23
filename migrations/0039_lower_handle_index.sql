-- The x-tag status endpoint (/api/x/status) resolves every handle on the
-- page being scanned via lower(a.handle::text) = any(...) — without an
-- expression index that is a ~25ms seq scan over all accounts per call,
-- and the extension calls it on every page scan/scroll. The plain
-- accounts_handle_idx can't serve the lower() form (handle is citext but
-- the queries compare through ::text).

create index accounts_lower_handle_idx on accounts ((lower(handle::text)));
