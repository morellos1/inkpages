-- Schema hardening from the 2026-07-21 audit:
-- 1. Provenance is structural: every identity edge and attestation MUST point
--    at the snapshot it was extracted from (brief rule 1). Both columns held
--    zero NULLs; this makes the invariant a constraint instead of a habit.
-- 2. In-force suppressions are unique per (target, reason) so a duplicate
--    insert can never make an opt-out unliftable (lifting updates all rows
--    anyway, but the constraint stops the ambiguity at the source).
-- 3. Expression indexes for the hot artist_events lookups (cluster self-heal,
--    admin-removed guard, reextract healing) — these were full scans of an
--    append-only table on every pipeline run.
-- 4. merged_into chains collapse: a redirect pointer always lands on a live
--    artist (cluster.py now maintains this on merge; repair any old chains).
-- 5. Suppression scope: an ACCOUNT-scoped suppression (e.g. impersonation)
--    now hides only that account; only an ARTIST-scoped suppression removes
--    the artist. Previously one bad member account nuked the whole artist.

alter table identity_edges alter column evidence_snapshot_id set not null;
alter table attestations alter column evidence_snapshot_id set not null;

create unique index suppressions_artist_reason_uniq
    on suppressions (artist_id, reason)
    where lifted_at is null and artist_id is not null;
create unique index suppressions_account_reason_uniq
    on suppressions (account_id, reason)
    where lifted_at is null and account_id is not null;

-- cluster.py self-heal: latest account_added per (artist, account).
create index artist_events_added_idx
    on artist_events (artist_id, ((details ->> 'account_id')::bigint), created_at desc)
    where event = 'account_added';
-- add_member / merge_artists admin-removed guard.
create index artist_events_removed_idx
    on artist_events (artist_id, ((details ->> 'account_id')::bigint))
    where event = 'account_removed';
-- reextract downstream healing: account_added by justifying edge id.
create index artist_events_added_edge_idx
    on artist_events (((details ->> 'edge_id')::bigint))
    where event = 'account_added';

-- Collapse any existing merged_into chains (loser -> merged loser -> keeper).
do $$
begin
    loop
        update artists a set merged_into = b.merged_into
        from artists b
        where a.merged_into = b.id and b.merged_into is not null;
        exit when not found;
    end loop;
end $$;

-- Publish view: account-scoped suppressions hide the account, not the artist.
-- Every per-account subquery now requires the account to be unsuppressed, and
-- the final filter drops the artist only for ARTIST-scoped suppressions.
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
          and not exists (select 1 from suppressions s
                          where s.lifted_at is null and s.account_id = ac.id)
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
          and not exists (select 1 from suppressions s
                          where s.lifted_at is null and s.account_id = ac.id)
    ) as accounts,
    exists (
        select 1
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        join content_flags cf on cf.account_id = aa.account_id
                             and cf.active and cf.flag = 'nsfw'
        where aa.artist_id = a.id and aa.removed_at is null
          and ac.status in ('active', 'unknown')
          and not exists (select 1 from suppressions s
                          where s.lifted_at is null and s.account_id = ac.id)
    ) as nsfw,
    (
        select max(ac.last_post_at)
        from artist_accounts aa
        join accounts ac on ac.id = aa.account_id
        where aa.artist_id = a.id and aa.removed_at is null
          and ac.status in ('active', 'unknown')
          and not exists (select 1 from suppressions s
                          where s.lifted_at is null and s.account_id = ac.id)
    ) as last_active_at,
    coalesce(
        (
            select max(ac.last_post_at)
            from artist_accounts aa
            join accounts ac on ac.id = aa.account_id
            where aa.artist_id = a.id and aa.removed_at is null
              and ac.status in ('active', 'unknown')
              and not exists (select 1 from suppressions s
                              where s.lifted_at is null and s.account_id = ac.id)
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
                      and not exists (select 1 from suppressions s
                                      where s.lifted_at is null and s.account_id = c2.id)
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
          and not exists (select 1 from suppressions s
                          where s.lifted_at is null and s.account_id = c.id)
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
          and not exists (select 1 from suppressions s
                          where s.lifted_at is null and s.account_id = ac.id)
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
          and not exists (select 1 from suppressions s
                          where s.lifted_at is null and s.account_id = ac.id)
    ) as sources
from artists a
where a.status = 'active'
  and a.merged_into is null
  and exists (select 1 from artist_accounts aa
              join accounts ac on ac.id = aa.account_id
              where aa.artist_id = a.id and aa.removed_at is null
                and ac.status in ('active', 'unknown')
                and not exists (select 1 from suppressions s
                                where s.lifted_at is null and s.account_id = ac.id))
  and not exists (
      select 1
      from suppressions s
      where s.lifted_at is null
        and s.artist_id = a.id
  );
