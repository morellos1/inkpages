-- Misskey (Mastodon-style JP social instances). Previously misskey.io/@handle
-- links fell through to the generic 'website' catch-all; now they resolve to a
-- first-class social platform with a stable local handle.

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('misskey', 'Misskey', 'social', false, 'https://misskey.io/@{handle}', 'JP fediverse; handle is the local username on misskey.io')
on conflict (slug) do nothing;

-- Reclassify existing accounts misfiled under 'website' that are really
-- misskey.io/@handle links. Skip any whose target handle already exists as a
-- misskey account (the unique (platform_id, native_id) / handle would collide);
-- re-extraction will heal those edges on the next pass.
with cand as (
    select a.id,
           (regexp_match(a.handle::text, 'misskey\.(?:io|design|art)/@([A-Za-z0-9_]+)'))[1] as h,
           row_number() over (
               partition by (regexp_match(a.handle::text, 'misskey\.(?:io|design|art)/@([A-Za-z0-9_]+)'))[1]
               order by a.id) as rn
    from accounts a
    where a.platform_id = (select id from platforms where slug = 'website')
      and a.handle::text ~* 'misskey\.(?:io|design|art)/@[A-Za-z0-9_]+'
)
update accounts a
set platform_id = (select id from platforms where slug = 'misskey'),
    handle = cand.h,
    profile_url = 'https://misskey.io/@' || cand.h
from cand
where cand.id = a.id and cand.rn = 1
  and not exists (
      select 1 from accounts b
      where b.platform_id = (select id from platforms where slug = 'misskey')
        and b.handle = cand.h::citext
  );
