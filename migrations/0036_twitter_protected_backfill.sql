-- Twitter protected (private) flag backfill (2026-07-23 eve).
--
-- The X API user object has always included `protected` in our field list
-- and every hydration stores the full object in account_snapshots.raw —
-- but nothing surfaced it. hydrate paths now write it into
-- accounts.platform_stats on every hydration; this backfills the whole
-- existing population from each account's LATEST snapshot, free.

update accounts a
set platform_stats = coalesce(a.platform_stats, '{}'::jsonb)
                     || jsonb_build_object('protected', (ls.raw ->> 'protected')::boolean)
from (select distinct on (s.account_id) s.account_id, s.raw
      from account_snapshots s
      where s.raw ? 'protected'
      order by s.account_id, s.captured_at desc) ls
where ls.account_id = a.id
  and (a.platform_stats is null
       or (a.platform_stats ->> 'protected') is distinct from (ls.raw ->> 'protected'));
