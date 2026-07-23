-- Junk link-artifact cleanup + anomaly threshold raise (2026-07-23).
--
-- 1) Malformed URL text minted accounts whose "handle" is a scheme fragment
--    (instagram.com/https), a reserved page word (tumblr.com/profile), a
--    doubled platform domain (instagram.com/instagram.com), or an
--    ellipsis-truncated handle swallowed by a dot-permitting charset
--    (izunee_artto...). extract.find_platform_links now rejects all of
--    these (_RESERVED_HANDLES + truncation-dot + platform-domain guards);
--    this migration retracts what's already in the graph. Edges from bio
--    snapshots would also fall out of the next reextract run, but hub-crawl
--    snapshots are excluded from reextract, so the retraction is done
--    directly here for all of them.
--
-- 2) ANOMALY_HUB_FANOUT 12 -> 25 and ANOMALY_HUB_ATTACHED 10 -> 20
--    (policy.py): the old bounds flagged legitimately link-rich personal
--    hubs faster than a human can review. Pending anomaly items that only
--    qualified under the old bounds auto-resolve below.

-- ---- 1. junk-handle accounts ---------------------------------------------

create temporary table junk_accounts on commit drop as
select a.id
from accounts a
join platforms p on p.id = a.platform_id
where
    -- scheme fragments / reserved page words as the whole handle
    (p.slug <> 'website'
     and lower(a.handle::text) in
         ('https', 'http', 'www', 'profile', 'share', 'home', 'index',
          'search', 'login', 'signup', 'account', 'accounts', 'explore',
          'watch', 'null', 'undefined'))
    -- ellipsis-truncated handles: truncation always leaves a trailing dot
    -- (or a literal … from display text). Mid-handle dots stay — yun..art
    -- is a real tiktok handle from the artist's own lit.link.
 or (a.handle::text like '%.' or a.handle::text like '%…%'
     or (p.slug = 'website' and a.handle::text like '%...%'))
    -- a handle that IS a platform's own domain = doubled/glued URL text
 or (p.slug not in ('website', 'bluesky')
     and lower(a.handle::text) in
         ('x.com', 'twitter.com', 'bsky.app', 'pixiv.net', 'skeb.jp',
          'instagram.com', 'tiktok.com', 'youtube.com', 'tumblr.com',
          'facebook.com', 'patreon.com', 'deviantart.com', 'artstation.com',
          'linktr.ee', 'carrd.co', 'ko-fi.com', 'gumroad.com', 'twitch.tv',
          'fanbox.cc', 'booth.pm', 'weibo.com'))
    -- bare platform domains stored as "website" accounts (domains that are
    -- in extract._NON_WEBSITE_DOMAINS now, minted before they were listed)
 or (p.slug = 'website'
     and lower(a.handle::text) in ('facebook.com', 'tiktok.com'));

-- Retract every present edge touching a junk account. The new extraction
-- guards mean no crawl/reextract can ever re-emit these.
update identity_edges e
set status = 'retracted'
where e.status = 'present'
  and (e.source_account_id in (select id from junk_accounts)
       or e.target_account_id in (select id from junk_accounts));

-- Close any memberships, with an admin event so clustering never re-attaches.
insert into artist_events (artist_id, event, actor, details)
select aa.artist_id, 'account_removed', 'admin:migration-0034',
       jsonb_build_object('account_id', aa.account_id,
                          'reason', 'junk_handle_cleanup')
from artist_accounts aa
where aa.removed_at is null
  and aa.account_id in (select id from junk_accounts);

update artist_accounts aa
set removed_at = now()
where aa.removed_at is null
  and aa.account_id in (select id from junk_accounts);

-- Hide the accounts themselves (kept for provenance; edges/snapshots stay).
update accounts
set status = 'hidden'
where id in (select id from junk_accounts) and status <> 'hidden';

-- ---- 2. anomaly threshold retro-resolution -------------------------------

-- Resolve pending hub-shape anomaly items that no longer meet the raised
-- thresholds and carry no other still-valid reason.
update review_items
set status = 'rejected', resolved_at = now(),
    decided_by = 'pipeline:threshold_raise'
where status = 'pending'
  and kind = 'other'
  and payload->>'type' = 'anomaly'
  and (payload->'reasons' ? 'hub_fanout' or payload->'reasons' ? 'hub_attached')
  and coalesce((payload->'reasons'->>'hub_fanout')::int, 0) < 25
  and coalesce((payload->'reasons'->>'hub_attached')::int, 0) < 20
  and not (payload->'reasons' ? 'related_connections')
  and not (payload->'reasons' ? 'cross_artist_refs');
