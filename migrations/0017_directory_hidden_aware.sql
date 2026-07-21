-- Make directory_entries consistently honor account status: a 'hidden' (or
-- suspended/deleted/deactivated) account must not keep its artist in the
-- directory, nor contribute avatar/sources/badges/commission/activity. The
-- accounts array already filtered on status; every other per-account subquery
-- and the artist-level existence check now do too.

create or replace view directory_entries as
select
    a.id          as artist_id,
    a.public_slug,
    a.display_name,
    a.region,
    exists (
        select 1
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        join attestations att on att.account_id = aa.account_id and att.active
        where aa.artist_id = a.id and aa.removed_at is null
          and ac.status in ('active', 'unknown')
    ) as no_ai_attested,
    (
        select jsonb_agg(
                   jsonb_build_object(
                       'platform', p.slug,
                       'handle', ac.handle,
                       'url', ac.profile_url,
                       'confidence', aa.confidence,
                       'last_post_at', ac.last_post_at,
                       'contact_email', ac.contact_email,
                       'commission_status', nullif(ac.commission_status, 'unknown'),
                       'stats', ac.platform_stats,
                       'avatar_url', ac.avatar_url
                   )
                   order by p.display_rank, p.slug, ac.handle
               )
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        join platforms p on p.id = ac.platform_id
        where aa.artist_id = a.id
          and aa.removed_at is null
          and ac.status in ('active', 'unknown')
    ) as accounts,
    exists (
        select 1
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        join content_flags cf on cf.account_id = aa.account_id
                             and cf.active and cf.flag = 'nsfw'
        where aa.artist_id = a.id and aa.removed_at is null
          and ac.status in ('active', 'unknown')
    ) as nsfw,
    (
        select max(ac.last_post_at)
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        where aa.artist_id = a.id and aa.removed_at is null
          and ac.status in ('active', 'unknown')
    ) as last_active_at,
    coalesce(
        (
            select max(ac.last_post_at)
            from artist_accounts aa
            join accounts ac on ac.id = aa.account_id
            where aa.artist_id = a.id and aa.removed_at is null
              and ac.status in ('active', 'unknown')
        ) < now() - interval '180 days',
        false
    ) as dormant,
    (
        select jsonb_build_object(
                   'status', c.commission_status,
                   'confidence', c.commission_confidence,
                   'checked_at', c.commission_checked_at)
        from artist_accounts aa
        join accounts c on c.id = aa.account_id
        where aa.artist_id = a.id and aa.removed_at is null
          and c.status in ('active', 'unknown')
          and c.commission_status <> 'unknown'
        order by c.commission_checked_at desc nulls last,
                 c.commission_confidence desc nulls last
        limit 1
    ) as commissions,
    a.language,
    (
        select ac.avatar_url
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        join platforms p on p.id = ac.platform_id
        where aa.artist_id = a.id and aa.removed_at is null
          and ac.status in ('active', 'unknown')
          and ac.avatar_url is not null
        order by p.display_rank, p.slug
        limit 1
    ) as avatar_url,
    (
        select array_remove(array_agg(distinct
                   case
                       when ac.discovered_via = 'skeb_ranking' then 'skeb'
                       when ac.discovered_via like 'bsky_%' then 'bluesky'
                       when ac.discovered_via in ('portfolioday', 'portfolioday_mention',
                                                  'twitter_list') then 'twitter'
                       when ac.discovered_via = 'pixiv_ranking' then 'pixiv'
                       when ac.discovered_via = 'patreon_ranking' then 'patreon'
                   end), null)
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        where aa.artist_id = a.id and aa.removed_at is null
          and ac.status in ('active', 'unknown')
    ) as sources
from artists a
where a.status = 'active'
  and a.merged_into is null
  and exists (select 1 from artist_accounts aa
              join accounts ac on ac.id = aa.account_id
              where aa.artist_id = a.id and aa.removed_at is null
                and ac.status in ('active', 'unknown'))
  and not exists (
      select 1
      from suppressions s
      where s.lifted_at is null
        and (s.artist_id = a.id
             or s.account_id in (select aa.account_id
                                 from artist_accounts aa
                                 where aa.artist_id = a.id and aa.removed_at is null))
  );
