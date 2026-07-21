-- Moderation (suppressions, community corrections), ranking runs, the X API
-- spend ledger, and the directory_entries publish view.

-- Suppressions keep an artist/account out of the directory while retaining the
-- row itself, so re-discovery can never silently re-add an opted-out artist.
create table suppressions (
    id           bigint generated always as identity primary key,
    artist_id    bigint references artists (id),
    account_id   bigint references accounts (id),
    reason       text not null
                 check (reason in ('opt_out', 'impersonation', 'ai_use_confirmed', 'other')),
    note         text,
    requested_by text,
    created_at   timestamptz not null default now(),
    lifted_at    timestamptz,  -- null = in force
    check (artist_id is not null or account_id is not null)
);

create index suppressions_artist_idx on suppressions (artist_id) where lifted_at is null;
create index suppressions_account_idx on suppressions (account_id) where lifted_at is null;

-- Community reports. An accepted 'ai_use' report leads to badge removal or
-- suppression — never to a published accusation.
create table corrections (
    id              bigint generated always as identity primary key,
    artist_id       bigint references artists (id),
    account_id      bigint references accounts (id),
    kind            text not null
                    check (kind in ('ai_use', 'wrong_link', 'impersonation',
                                    'opt_out', 'stale_info', 'other')),
    body            text not null,
    evidence_url    text,
    contact         text,
    status          text not null default 'pending'
                    check (status in ('pending', 'accepted', 'rejected')),
    resolution_note text,
    created_at      timestamptz not null default now(),
    resolved_at     timestamptz
);

create index corrections_pending_idx on corrections (created_at) where status = 'pending';

-- Each stratified cut is a recorded run, so past cuts remain queryable and the
-- border zone drives the quarterly refresh set.
create table ranking_runs (
    id     bigint generated always as identity primary key,
    scope  text not null check (scope in ('twitter', 'bluesky')),
    ran_at timestamptz not null default now(),
    params jsonb
);

create table ranking_entries (
    run_id       bigint not null references ranking_runs (id),
    artist_id    bigint not null references artists (id),
    region       text not null check (region in ('eastern', 'western', 'unknown')),
    metric_value bigint not null,
    rank         integer not null,
    included     boolean not null,
    border_zone  boolean not null default false,
    primary key (run_id, artist_id)
);

create index ranking_entries_artist_idx on ranking_entries (artist_id);

-- Spend ledger for pay-per-use APIs (the X API budget caps are enforced by
-- querying this, not by hoping).
create table api_usage (
    id             bigint generated always as identity primary key,
    service        text not null,
    endpoint       text not null,
    units          integer not null,
    est_cost_cents integer not null default 0,
    occurred_at    timestamptz not null default now(),
    note           text
);

create index api_usage_service_idx on api_usage (service, occurred_at);

-- The ONLY publish surface. Everything exported to the public directory comes
-- through here: active, non-suppressed artists with their live member accounts
-- and the derived no-AI badge. This view must never reference discovery_hints
-- (scripts/smoke.sql asserts that structurally via pg_depend).
create view directory_entries as
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
                       -- display_only platforms (Instagram): handle shown as
                       -- text, no link — we never verified the profile exists.
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
    ) as accounts
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
