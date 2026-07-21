-- Edge claims (same-person vs related accounts), bio-mention evidence, and
-- account activity tracking.

-- claim: what the edge asserts. 'same_person' edges are identity claims and
-- can cluster; 'related' edges (partner, pfp artist, friend, bare mention)
-- are knowledge-graph connections and never merge artists.
alter table identity_edges
    add column claim text not null default 'same_person'
        check (claim in ('same_person', 'related')),
    add column relation_hint text;

alter table identity_edges drop constraint identity_edges_evidence_type_check;
alter table identity_edges add constraint identity_edges_evidence_type_check
    check (evidence_type in ('bio_link', 'link_hub', 'profile_field', 'pinned_post',
                             'same_handle', 'bio_mention'));

-- Latest known post/repost time (Bluesky: author feed head; Twitter: decoded
-- from most_recent_tweet_id's snowflake — free with the user read).
alter table accounts add column last_post_at timestamptz;

-- Publish view: accounts gain last_post_at; artist-level last_active_at and
-- dormant (no activity in 180 days — mirror of policy.DORMANT_AFTER_DAYS).
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
                       'url', case when p.display_only then null else ac.profile_url end,
                       'display_only', p.display_only,
                       'confidence', aa.confidence,
                       'last_post_at', ac.last_post_at
                   )
                   order by p.slug, ac.handle
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
    ) as dormant
from artists a
where a.status = 'active'
  and a.merged_into is null
  and not exists (
      select 1
      from suppressions s
      where s.lifted_at is null
        and (s.artist_id = a.id
             or s.account_id in (select aa.account_id
                                 from artist_accounts aa
                                 where aa.artist_id = a.id and aa.removed_at is null))
  );
