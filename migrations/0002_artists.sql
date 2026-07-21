-- Artist entities (materialized clusters), membership with history, audit
-- events, and per-account no-AI attestations.

create table artists (
    id                 bigint generated always as identity primary key,
    public_slug        text not null unique,
    display_name       text not null,
    region             text not null default 'unknown'
                       check (region in ('eastern', 'western', 'unknown')),
    region_confidence  real,
    region_source      text not null default 'auto'
                       check (region_source in ('auto', 'manual')),
    primary_account_id bigint references accounts (id),
    status             text not null default 'active'
                       check (status in ('active', 'needs_review', 'suppressed')),
    -- Set when this artist row was merged into another; the row is kept so the
    -- old public slug can redirect and the merge stays auditable.
    merged_into        bigint references artists (id),
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

-- Cluster membership with history: rows are closed (removed_at) rather than
-- deleted, so splits and corrections leave a trail.
create table artist_accounts (
    id         bigint generated always as identity primary key,
    artist_id  bigint not null references artists (id),
    account_id bigint not null references accounts (id),
    confidence text not null check (confidence in ('near_proof', 'strong', 'weak')),
    added_by   text not null check (added_by in ('clustering', 'human')),
    added_at   timestamptz not null default now(),
    removed_at timestamptz
);

-- An account belongs to at most one live artist.
create unique index artist_accounts_live_uniq
    on artist_accounts (account_id) where removed_at is null;
create index artist_accounts_artist_idx on artist_accounts (artist_id);

create table artist_events (
    id         bigint generated always as identity primary key,
    artist_id  bigint not null references artists (id),
    event      text not null
               check (event in ('created', 'merged', 'split', 'account_added',
                                'account_removed', 'suppressed', 'unsuppressed',
                                'region_override', 'slug_changed')),
    actor      text not null,  -- 'pipeline', 'admin:<name>', 'community'
    details    jsonb,
    created_at timestamptz not null default now()
);

create index artist_events_artist_idx on artist_events (artist_id, created_at);

-- Per-ACCOUNT no-AI signals, always the artist's own self-attestation. The
-- artist-level badge is derived (any active signal on any live member account),
-- and first_seen/last_seen make signal *removal* detectable so a badge drops at
-- the next refresh instead of lingering.
create table attestations (
    id                   bigint generated always as identity primary key,
    account_id           bigint not null references accounts (id),
    signal               text not null
                         check (signal in ('bio_tag', 'glaze_mention', 'nightshade_mention',
                                           'cara_membership', 'xfolio_membership',
                                           'deviantart_noai_flag', 'bsky_labeler')),
    matched_text         text,  -- e.g. '#NoAI', 'AI学習禁止', labeler DID
    evidence_snapshot_id bigint references account_snapshots (id),
    evidence_url         text,
    first_seen           timestamptz not null default now(),
    last_seen            timestamptz not null default now(),
    active               boolean not null default true
);

create unique index attestations_uniq
    on attestations (account_id, signal, coalesce(matched_text, ''));
create index attestations_account_active_idx
    on attestations (account_id) where active;
