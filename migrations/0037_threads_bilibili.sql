-- Threads and Bilibili as first-class display-only platforms (2026-07-23).
-- Their profile links previously fell through to the generic 'website'
-- catch-all (29 threads.net + 47 space.bilibili.com rows at count time).
-- Neither is fetched — display-only like Instagram/Weibo; the link itself
-- is the artist-published identity claim.

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('threads',  'Threads',  'social', true, 'https://www.threads.net/@{handle}',   'DISPLAY ONLY: never fetched; @handle mirrors the owner''s Instagram.'),
    ('bilibili', 'Bilibili', 'social', true, 'https://space.bilibili.com/{native_id}', 'DISPLAY ONLY: never fetched; uid space links are stable.')
on conflict (slug) do nothing;

update platforms set display_rank = 10 where slug in ('threads', 'bilibili');

-- Reclassify existing accounts misfiled under 'website'. Collision-safe like
-- migrations 0018/0020: skip handles already existing on the new platform;
-- re-extraction heals those edges on the next pass.

with cand as (
    select a.id,
           (regexp_match(a.handle::text, 'threads\.(?:net|com)/@([a-z0-9._]{1,30})'))[1] as h,
           row_number() over (
               partition by (regexp_match(a.handle::text, 'threads\.(?:net|com)/@([a-z0-9._]{1,30})'))[1]
               order by a.id) as rn
    from accounts a
    where a.platform_id = (select id from platforms where slug = 'website')
      and a.handle::text ~* 'threads\.(?:net|com)/@[a-z0-9._]{1,30}'
)
update accounts a
set platform_id = (select id from platforms where slug = 'threads'),
    handle = cand.h,
    profile_url = 'https://www.threads.net/@' || cand.h
from cand
where cand.id = a.id and cand.rn = 1
  and not exists (
      select 1 from accounts b
      where b.platform_id = (select id from platforms where slug = 'threads')
        and b.handle = cand.h::citext
  );

with cand as (
    select a.id,
           (regexp_match(a.handle::text, 'space\.bilibili\.com/(\d+)'))[1] as uid,
           row_number() over (
               partition by (regexp_match(a.handle::text, 'space\.bilibili\.com/(\d+)'))[1]
               order by a.id) as rn
    from accounts a
    where a.platform_id = (select id from platforms where slug = 'website')
      and a.handle::text ~* 'space\.bilibili\.com/\d+'
)
update accounts a
set platform_id = (select id from platforms where slug = 'bilibili'),
    native_id = cand.uid,
    handle = cand.uid,
    profile_url = 'https://space.bilibili.com/' || cand.uid
from cand
where cand.id = a.id and cand.rn = 1
  and not exists (
      select 1 from accounts b
      where b.platform_id = (select id from platforms where slug = 'bilibili')
        and (b.native_id = cand.uid or b.handle = cand.uid::citext)
  );
