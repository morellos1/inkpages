-- Core identity graph: platforms, accounts, snapshots, identity edges, and the
-- quarantined discovery-hint queue. Design rationale: docs/schema.md.

create extension if not exists citext;

create table platforms (
    id                   smallint generated always as identity primary key,
    slug                 text not null unique,
    display_name         text not null,
    kind                 text not null
                         check (kind in ('social', 'portfolio', 'commission', 'support', 'link_hub')),
    -- display_only platforms (Instagram) are surfaced as text in the directory
    -- but never fetched, hydrated, or used as edge evidence.
    display_only         boolean not null default false,
    profile_url_template text,
    notes                text
);

create table accounts (
    id                bigint generated always as identity primary key,
    platform_id       smallint not null references platforms (id),
    -- Platform-native stable id (Twitter numeric id, Bluesky DID). Handles are
    -- mutable and recyclable; native_id is the real identity where one exists.
    native_id         text,
    handle            citext not null,
    display_name      text,
    profile_url       text,
    status            text not null default 'unknown'
                      check (status in ('active', 'suspended', 'deleted', 'deactivated', 'unknown')),
    followers_count   bigint,
    -- Detected bio languages with scores, written by the extraction stage;
    -- input to the region classifier.
    bio_langs         jsonb,
    discovered_via    text not null,
    discovery_details jsonb,
    first_seen        timestamptz not null default now(),
    last_hydrated     timestamptz
);

create unique index accounts_platform_native_uniq
    on accounts (platform_id, native_id) where native_id is not null;
-- Handle-only uniqueness applies until we learn the native id; handle rows and
-- native-id rows for the same identity are merged by the hydration stage.
create unique index accounts_platform_handle_uniq
    on accounts (platform_id, handle) where native_id is null;
create index accounts_handle_idx on accounts (handle);

-- Append-only hydration observations. All downstream extraction (links,
-- attestations, languages) is a pure function of these rows and re-runnable.
create table account_snapshots (
    id              bigint generated always as identity primary key,
    account_id      bigint not null references accounts (id),
    captured_at     timestamptz not null default now(),
    bio_text        text,
    display_name    text,
    followers_count bigint,
    following_count bigint,
    raw             jsonb,
    fetch_source    text not null
);

create index account_snapshots_account_idx
    on account_snapshots (account_id, captured_at desc);

-- The claim graph. Each row is one directed, artist-published identity claim
-- with its evidence. Reciprocity is computed at clustering time, never stored.
create table identity_edges (
    id                   bigint generated always as identity primary key,
    source_account_id    bigint not null references accounts (id),
    target_account_id    bigint not null references accounts (id),
    evidence_type        text not null
                         check (evidence_type in
                                ('bio_link', 'link_hub', 'profile_field', 'pinned_post', 'same_handle')),
    evidence_snapshot_id bigint references account_snapshots (id),
    evidence_url         text,
    matched_text         text,
    first_seen           timestamptz not null default now(),
    last_verified        timestamptz,
    status               text not null default 'present'
                         check (status in ('present', 'stale', 'retracted')),
    check (source_account_id <> target_account_id),
    unique (source_account_id, target_account_id, evidence_type)
);

create index identity_edges_target_idx on identity_edges (target_account_id);

-- QUARANTINE: third-party identity assertions (boorus etc.). Nothing
-- publishable may ever join this table. Verification of a hint fetches the
-- artist's own bio/hub and, on success, writes an ordinary identity_edge whose
-- evidence points only at the artist's own snapshot — the hint itself never
-- enters directory lineage.
create table discovery_hints (
    id                   bigint generated always as identity primary key,
    source               text not null,
    hinted_platform_id   smallint references platforms (id),
    hinted_handle_or_url text not null,
    payload              jsonb,
    status               text not null default 'pending'
                         check (status in ('pending', 'verified', 'rejected', 'expired')),
    created_at           timestamptz not null default now(),
    resolved_at          timestamptz
);

create index discovery_hints_pending_idx
    on discovery_hints (created_at) where status = 'pending';
