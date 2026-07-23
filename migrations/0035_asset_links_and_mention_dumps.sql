-- Asset-link junk + DA mention-dump cleanup (2026-07-23 eve).
--
-- 1) Page markup minted "website" accounts out of embedded files: DA avatar
--    images (a.deviantart.net/avatars-big/*.gif), wixmp/wix-file CDN
--    images, adsbygoogle script tags (pagead2.googlesyndication.com/...js),
--    and fav.me per-deviation shortlinks (artwork permalinks, not anyone's
--    site). extract.find_website_links now rejects static-asset extensions
--    (_ASSET_EXT) and the domains joined _NON_WEBSITE_DOMAINS; this
--    retracts what's already stored.
--
-- 2) Group-runner DeviantArt about pages list dozens of members/watchers;
--    each became a related/same_platform_mention connection (one artist
--    carried 24). discover_deviantart now drops DA→DA mentions wholesale
--    when a page mentions > 5 distinct deviants (MAX_MENTIONS_PER_PAGE);
--    this applies the same bound to stored edges. DA about snapshots are
--    excluded from reextract, so the retro-pass lives here.

-- ---- 1. asset/infra website accounts -------------------------------------

create temporary table junk_sites on commit drop as
select a.id
from accounts a
join platforms p on p.id = a.platform_id
where p.slug = 'website'
  and (
    -- static-asset file paths (mirrors extract._ASSET_EXT)
    a.handle::text ~* '\.(js|mjs|css|json|xml|png|jpe?g|gif|webp|avif|svg|ico|bmp|woff2?|ttf|otf|eot|mp[34]|webm|mov|wav|ogg|pdf|zip|rar|7z)$'
    -- ad-network / CDN / content-shortlink domains (host part of the handle)
    or split_part(a.handle::text, '/', 1) ~* '(^|\.)(googlesyndication\.com|doubleclick\.net|googleadservices\.com|deviantart\.net|wixmp\.com|usrfiles\.com|filesusr\.com|fav\.me)$'
  );

update identity_edges e
set status = 'retracted'
where e.status = 'present'
  and (e.source_account_id in (select id from junk_sites)
       or e.target_account_id in (select id from junk_sites));

insert into artist_events (artist_id, event, actor, details)
select aa.artist_id, 'account_removed', 'admin:migration-0035',
       jsonb_build_object('account_id', aa.account_id,
                          'reason', 'asset_link_cleanup')
from artist_accounts aa
where aa.removed_at is null
  and aa.account_id in (select id from junk_sites);

update artist_accounts
set removed_at = now()
where removed_at is null
  and account_id in (select id from junk_sites);

update accounts
set status = 'hidden'
where id in (select id from junk_sites) and status <> 'hidden';

-- ---- 2. DA mention-dump edges --------------------------------------------

update identity_edges e
set status = 'retracted'
where e.status = 'present'
  and e.relation_hint = 'same_platform_mention'
  and e.source_account_id in (
      select e2.source_account_id
      from identity_edges e2
      join accounts sa on sa.id = e2.source_account_id
      join platforms sp on sp.id = sa.platform_id
      where e2.status = 'present'
        and e2.relation_hint = 'same_platform_mention'
        and sp.slug = 'deviantart'
      group by e2.source_account_id
      having count(distinct e2.target_account_id) > 5);
