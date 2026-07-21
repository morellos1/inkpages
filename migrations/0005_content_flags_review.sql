-- Content flags (NSFW/18+ self-signals, mirroring the attestations model) and
-- the human review queue for clustering decisions.

-- Like attestations, content flags are only ever the artist's own published
-- signals (bio markers, platform self-labels), displayed as their claim.
create table content_flags (
    id                   bigint generated always as identity primary key,
    account_id           bigint not null references accounts (id),
    flag                 text not null check (flag in ('nsfw')),
    signal               text not null
                         check (signal in ('bio_marker', 'self_label', 'platform_flag')),
    matched_text         text,  -- e.g. '🔞', 'R-18', bluesky self-label val
    evidence_snapshot_id bigint references account_snapshots (id),
    first_seen           timestamptz not null default now(),
    last_seen            timestamptz not null default now(),
    active               boolean not null default true
);

create unique index content_flags_uniq
    on content_flags (account_id, flag, signal, coalesce(matched_text, ''));
create index content_flags_account_active_idx
    on content_flags (account_id) where active;

-- Clustering decisions that need a human: one-directional attaches to
-- prominent targets (impersonation risk), proposed merges of existing
-- artists, same-handle suggestions.
create table review_items (
    id              bigint generated always as identity primary key,
    kind            text not null
                    check (kind in ('one_directional_attach', 'cluster_merge',
                                    'same_handle', 'other')),
    payload         jsonb not null,
    status          text not null default 'pending'
                    check (status in ('pending', 'approved', 'rejected')),
    created_at      timestamptz not null default now(),
    resolved_at     timestamptz,
    decided_by      text,
    resolution_note text
);

create index review_items_pending_idx on review_items (created_at) where status = 'pending';

-- Add the derived nsfw flag to the publish surface (new column appended last;
-- existing columns unchanged).
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
                       'confidence', aa.confidence
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
    ) as nsfw
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
