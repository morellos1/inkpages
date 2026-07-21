-- Commission status per account (with confidence and as-of date), contact
-- emails, and the Coloso platform.

alter table accounts
    add column contact_email text,
    add column commission_status text not null default 'unknown'
        check (commission_status in ('open', 'closed', 'waitlist', 'unknown')),
    add column commission_confidence real,
    add column commission_detail text,     -- the matched phrase, kept as evidence
    add column commission_checked_at timestamptz;  -- snapshot date: staleness is visible

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('coloso', 'Coloso', 'support', false, null, 'Art course platform; links are course pages');

-- Publish view: per-account contact/commission info; artist-level commissions
-- picks the FRESHEST non-unknown signal across member accounts (recency beats
-- status or confidence — stale "open" is the failure mode that matters).
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
                       'last_post_at', ac.last_post_at,
                       'contact_email', ac.contact_email,
                       'commission_status', nullif(ac.commission_status, 'unknown')
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
    ) as commissions
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
