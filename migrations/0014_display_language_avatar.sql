-- Platform display ordering, artist language, avatars, and the
-- memberless-artist guard on the publish view.

alter table platforms add column display_rank int not null default 10;
update platforms set display_rank = 0 where slug = 'twitter';
update platforms set display_rank = 1 where slug = 'bluesky';
update platforms set display_rank = 2 where slug in ('linktree', 'carrd', 'potofu', 'litlink', 'biosite');
update platforms set display_rank = 3 where slug = 'skeb';
update platforms set display_rank = 4 where slug = 'pixiv';
update platforms set display_rank = 20 where slug = 'website';

alter table artists add column language text not null default 'unknown'
    check (language in ('ja', 'en', 'ko', 'zh', 'unknown'));

alter table accounts add column avatar_url text;

create or replace view directory_entries as
select
    a.id          as artist_id,
    a.public_slug,
    a.display_name,
    a.region,
    exists (
        select 1
        from artist_accounts aa
        join attestations att on att.account_id = aa.account_id and att.active
        where aa.artist_id = a.id and aa.removed_at is null
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
        join content_flags cf on cf.account_id = aa.account_id
                             and cf.active and cf.flag = 'nsfw'
        where aa.artist_id = a.id and aa.removed_at is null
    ) as nsfw,
    (
        select max(ac.last_post_at)
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        where aa.artist_id = a.id and aa.removed_at is null
    ) as last_active_at,
    coalesce(
        (
            select max(ac.last_post_at)
            from artist_accounts aa
            join accounts ac on ac.id = aa.account_id
            where aa.artist_id = a.id and aa.removed_at is null
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
          and ac.avatar_url is not null
        order by p.display_rank, p.slug
        limit 1
    ) as avatar_url
from artists a
where a.status = 'active'
  and a.merged_into is null
  and exists (select 1 from artist_accounts aa
              where aa.artist_id = a.id and aa.removed_at is null)
  and not exists (
      select 1
      from suppressions s
      where s.lifted_at is null
        and (s.artist_id = a.id
             or s.account_id in (select aa.account_id
                                 from artist_accounts aa
                                 where aa.artist_id = a.id and aa.removed_at is null))
  );
