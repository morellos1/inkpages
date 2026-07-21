-- Split the artist-level `commissions` object into per-source signals so the
-- UI can show "skeb open" / "pixiv open" (platform-authoritative flags,
-- detail prefixed skeb:/pixiv:) separately from "comms open" (bio-attested),
-- with the check date exposed once as checked_at instead of riding the chip.

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
                       'display_name', ac.display_name,
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
        select case when count(*) > 0 then
            jsonb_build_object(
                'skeb_open', bool_or(p.slug = 'skeb'
                                     and c.commission_status = 'open'
                                     and c.commission_detail like 'skeb:%'),
                'pixiv_open', bool_or(p.slug = 'pixiv'
                                      and c.commission_status = 'open'
                                      and c.commission_detail like 'pixiv:%'),
                'bio_status', (
                    select c2.commission_status
                    from artist_accounts aa2
                    join accounts c2 on c2.id = aa2.account_id
                    where aa2.artist_id = a.id and aa2.removed_at is null
                      and c2.status in ('active', 'unknown')
                      and c2.commission_status <> 'unknown'
                      and coalesce(c2.commission_detail, '') not like 'skeb:%'
                      and coalesce(c2.commission_detail, '') not like 'pixiv:%'
                    order by c2.commission_checked_at desc nulls last,
                             c2.commission_confidence desc nulls last
                    limit 1
                ),
                'checked_at', max(c.commission_checked_at)
            ) end
        from artist_accounts aa
        join accounts c on c.id = aa.account_id
        join platforms p on p.id = c.platform_id
        where aa.artist_id = a.id and aa.removed_at is null
          and c.status in ('active', 'unknown')
          and c.commission_status <> 'unknown'
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
                       when ac.discovered_via in ('pixiv_ranking', 'pixiv_tag_search') then 'pixiv'
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
