-- Three JP profile-card / link-hub services promoted from the generic
-- 'website' catch-all to first-class crawlable hubs (crawl_links treats them
-- like linktree): profcard.info (opaque per-user uid), twpf.jp (handle mirrors
-- the owner's Twitter handle), tsunagu.cloud (/users/<handle>).
--
-- display_rank 15, NOT 2 like the older hubs: rank feeds the directory name
-- derivation (migration 0024), and a profcard uid ("u/0NwmQEDVic...") must
-- never become an artist's displayed name.

insert into platforms (slug, display_name, kind, display_only, profile_url_template, display_rank, notes) values
    ('profcard', 'Profcard', 'link_hub', false, 'https://profcard.info/u/{handle}', 15, 'JP profile card; handle is the opaque per-user uid'),
    ('twpf',     'Twpf',     'link_hub', false, 'https://twpf.jp/{handle}',          15, 'Twitter profile extension; handle mirrors the twitter handle'),
    ('tsunagu',  'TSUNAGU',  'link_hub', false, 'https://tsunagu.cloud/users/{handle}', 15, 'JP creator link hub')
on conflict (slug) do nothing;

-- Reclassify existing accounts misfiled under 'website' (same shape as the
-- 0018 misskey reclassify): pull the handle out of the stored URL, keep one
-- row per handle, skip collisions with already-existing rows on the new
-- platform; re-extraction heals the rest.
do $$
declare
    spec record;
begin
    for spec in
        select * from (values
            ('profcard', 'profcard\.info/u/([A-Za-z0-9]+)',        'https://profcard.info/u/'),
            ('twpf',     'twpf\.jp/([A-Za-z0-9_]{1,15})',          'https://twpf.jp/'),
            ('tsunagu',  'tsunagu\.cloud/users/([A-Za-z0-9_.-]+)', 'https://tsunagu.cloud/users/')
        ) as t(slug, pat, url_prefix)
    loop
        execute format($q$
            with cand as (
                select a.id,
                       (regexp_match(coalesce(a.profile_url, a.handle::text), %L))[1] as h,
                       row_number() over (
                           partition by (regexp_match(coalesce(a.profile_url, a.handle::text), %L))[1]
                           order by a.id) as rn
                from accounts a
                where a.platform_id = (select id from platforms where slug = 'website')
                  and coalesce(a.profile_url, a.handle::text) ~ %L
            )
            update accounts a
            set platform_id = (select id from platforms where slug = %L),
                handle = cand.h,
                profile_url = %L || cand.h
            from cand
            where cand.id = a.id and cand.rn = 1 and cand.h is not null
              and not exists (
                  select 1 from accounts b
                  where b.platform_id = (select id from platforms where slug = %L)
                    and b.handle = cand.h::citext
              )
        $q$, spec.pat, spec.pat, spec.pat, spec.slug, spec.url_prefix, spec.slug);
    end loop;
end $$;
