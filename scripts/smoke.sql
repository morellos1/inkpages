-- Smoke test for the schema and the directory_entries publish rules.
-- Run against a freshly migrated database:
--   psql "$DATABASE_URL" -f scripts/smoke.sql
-- Everything is rolled back at the end; the database is left untouched.

begin;

-- Fixture 1: a real artist with reciprocal Twitter <-> Pixiv bio links, a
-- display-only Instagram handle harvested from her Twitter bio, and a #NoAI tag.
insert into accounts (platform_id, native_id, handle, display_name, profile_url, status, followers_count, discovered_via)
values
    ((select id from platforms where slug = 'twitter'),   '111', 'inkwitch',    'Ink Witch', 'https://x.com/inkwitch',           'active',  52000, 'portfolioday'),
    ((select id from platforms where slug = 'pixiv'),     '222', 'inkwitch_px', 'インク魔女',  'https://www.pixiv.net/users/222', 'active',  30000, 'pixiv_ranking'),
    ((select id from platforms where slug = 'instagram'), null,  'inkwitch.ig', null,        null,                               'unknown', null,  'bio_link'),
-- Fixture 2: an artist who requested removal (opt-out suppression).
    ((select id from platforms where slug = 'twitter'),   '333', 'brushlord',   'Brush Lord', 'https://x.com/brushlord',         'active',  90000, 'twitter_list');

insert into account_snapshots (account_id, bio_text, followers_count, fetch_source)
values
    ((select id from accounts where handle = 'inkwitch'),    'Illustrator. #NoAI — pixiv.net/users/222 / IG: inkwitch.ig', 52000, 'x:users/by'),
    ((select id from accounts where handle = 'inkwitch_px'), 'AI学習禁止 / x.com/inkwitch',                                 30000, 'pixiv:profile');

insert into identity_edges (source_account_id, target_account_id, evidence_type, evidence_snapshot_id, evidence_url)
values
    ((select id from accounts where handle = 'inkwitch'),
     (select id from accounts where handle = 'inkwitch_px'),
     'bio_link',
     (select s.id from account_snapshots s join accounts a on a.id = s.account_id where a.handle = 'inkwitch'),
     'https://www.pixiv.net/users/222'),
    ((select id from accounts where handle = 'inkwitch_px'),
     (select id from accounts where handle = 'inkwitch'),
     'bio_link',
     (select s.id from account_snapshots s join accounts a on a.id = s.account_id where a.handle = 'inkwitch_px'),
     'https://x.com/inkwitch'),
    ((select id from accounts where handle = 'inkwitch'),
     (select id from accounts where handle = 'inkwitch.ig'),
     'bio_link',
     (select s.id from account_snapshots s join accounts a on a.id = s.account_id where a.handle = 'inkwitch'),
     'https://www.instagram.com/inkwitch.ig');

insert into artists (public_slug, display_name, region, region_source, status, primary_account_id)
values
    ('inkwitch',  'Ink Witch',  'eastern', 'auto', 'active', (select id from accounts where handle = 'inkwitch')),
    ('brushlord', 'Brush Lord', 'western', 'auto', 'active', (select id from accounts where handle = 'brushlord'));

insert into artist_accounts (artist_id, account_id, confidence, added_by)
values
    ((select id from artists where public_slug = 'inkwitch'),  (select id from accounts where handle = 'inkwitch'),    'near_proof', 'clustering'),
    ((select id from artists where public_slug = 'inkwitch'),  (select id from accounts where handle = 'inkwitch_px'), 'near_proof', 'clustering'),
    ((select id from artists where public_slug = 'inkwitch'),  (select id from accounts where handle = 'inkwitch.ig'), 'strong',     'clustering'),
    ((select id from artists where public_slug = 'brushlord'), (select id from accounts where handle = 'brushlord'),   'near_proof', 'clustering');

insert into attestations (account_id, signal, matched_text, evidence_snapshot_id)
values
    ((select id from accounts where handle = 'inkwitch'), 'bio_tag', '#NoAI',
     (select s.id from account_snapshots s join accounts a on a.id = s.account_id where a.handle = 'inkwitch'));

insert into content_flags (account_id, flag, signal, matched_text, evidence_snapshot_id)
values
    ((select id from accounts where handle = 'inkwitch_px'), 'nsfw', 'bio_marker', 'R-18',
     (select s.id from account_snapshots s join accounts a on a.id = s.account_id where a.handle = 'inkwitch_px'));

insert into suppressions (artist_id, reason, note, requested_by)
values
    ((select id from artists where public_slug = 'brushlord'), 'opt_out', 'Email request 2026-07-01', 'artist via contact form');

-- A quarantined third-party hint; must never influence directory output.
insert into discovery_hints (source, hinted_platform_id, hinted_handle_or_url, payload)
values
    ('danbooru', (select id from platforms where slug = 'twitter'), 'inkwitch',
     '{"artist_entry": "ink_witch", "urls": ["https://x.com/inkwitch"]}');

do $$
declare
    n int;
begin
    -- Scoped to the fixture slugs so the smoke test also runs against a
    -- populated dev database, not only a fresh one.
    select count(*) into n from directory_entries
    where public_slug in ('inkwitch', 'brushlord');
    if n <> 1 then
        raise exception 'expected exactly 1 fixture entry (suppressed artist excluded), got %', n;
    end if;

    select count(*) into n from directory_entries where public_slug = 'inkwitch' and no_ai_attested;
    if n <> 1 then
        raise exception 'inkwitch should be listed with the no-AI badge';
    end if;

    select count(*) into n from directory_entries where public_slug = 'inkwitch' and nsfw;
    if n <> 1 then
        raise exception 'inkwitch should carry the derived nsfw flag (R-18 on her pixiv account)';
    end if;

    -- Display-only policy: the Instagram handle appears, but unlinked.
    select count(*) into n
    from directory_entries, jsonb_array_elements(accounts) e
    where public_slug = 'inkwitch'
      and e ->> 'platform' = 'instagram'
      and e ->> 'url' is null;
    if n <> 1 then
        raise exception 'instagram handle should be present but unlinked (display_only)';
    end if;

    -- Lineage rule, checked structurally: the publish view must not depend on
    -- discovery_hints. Positive control first, to prove the mechanism works.
    select count(*) into n
    from pg_depend d
    join pg_rewrite r on d.classid = 'pg_rewrite'::regclass and d.objid = r.oid
    where r.ev_class = 'directory_entries'::regclass
      and d.refclassid = 'pg_class'::regclass
      and d.refobjid = 'artists'::regclass;
    if n = 0 then
        raise exception 'dependency check is broken: view should depend on artists';
    end if;

    select count(*) into n
    from pg_depend d
    join pg_rewrite r on d.classid = 'pg_rewrite'::regclass and d.objid = r.oid
    where r.ev_class = 'directory_entries'::regclass
      and d.refclassid = 'pg_class'::regclass
      and d.refobjid = 'discovery_hints'::regclass;
    if n <> 0 then
        raise exception 'LINEAGE VIOLATION: directory_entries depends on discovery_hints';
    end if;

    -- Provenance is structural (migration 0022): edges and attestations
    -- cannot exist without an evidence snapshot.
    select count(*) into n
    from information_schema.columns
    where table_schema = 'public'
      and table_name in ('identity_edges', 'attestations')
      and column_name = 'evidence_snapshot_id'
      and is_nullable = 'YES';
    if n <> 0 then
        raise exception 'evidence_snapshot_id must be NOT NULL on identity_edges and attestations';
    end if;

    -- merged_into never chains: a redirect pointer always lands on a live artist.
    select count(*) into n
    from artists a join artists b on b.id = a.merged_into
    where b.merged_into is not null;
    if n <> 0 then
        raise exception 'merged_into chain found: % redirect(s) point at a merged artist', n;
    end if;

    raise notice 'smoke test passed';
end
$$;

-- Suppression scope (migration 0022): an ACCOUNT-scoped suppression hides the
-- account but never the whole artist; only artist-scoped removes the artist.
insert into suppressions (account_id, reason, note, requested_by)
values ((select id from accounts where handle = 'inkwitch.ig'),
        'impersonation', 'fake IG', 'smoke');

do $$
declare
    n int;
begin
    select count(*) into n from directory_entries where public_slug = 'inkwitch';
    if n <> 1 then
        raise exception 'account-scoped suppression must not remove the artist';
    end if;

    select count(*) into n
    from directory_entries, jsonb_array_elements(accounts) e
    where public_slug = 'inkwitch' and e ->> 'platform' = 'instagram';
    if n <> 0 then
        raise exception 'account-scoped suppression must hide the suppressed account';
    end if;

    raise notice 'suppression-scope test passed';
end
$$;

select public_slug, display_name, region, no_ai_attested, nsfw, jsonb_pretty(accounts) as accounts
from directory_entries;

rollback;
