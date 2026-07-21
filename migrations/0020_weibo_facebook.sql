-- Weibo and Facebook as first-class identity platforms. Previously their
-- profile links fell through to the generic 'website' catch-all. Neither is
-- ever fetched (no API access worth having) — display-only like Instagram;
-- the handle itself is the artist-published identity claim.

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('weibo',    'Weibo',    'social', true, 'https://weibo.com/u/{native_id}',    'DISPLAY ONLY: never fetched; uid links are stable, name links use the vanity handle.'),
    ('facebook', 'Facebook', 'social', true, 'https://www.facebook.com/{handle}',  'DISPLAY ONLY: never fetched; vanity handles only, numeric profile.php ids kept as native_id.')
on conflict (slug) do nothing;

update platforms set display_rank = 10 where slug in ('weibo', 'facebook');

-- Reclassify existing accounts misfiled under 'website'. Same collision-safe
-- pattern as migration 0018: skip anything whose target handle already exists
-- on the new platform; re-extraction heals those edges on the next pass.

-- Weibo uid form: weibo.com/u/123, weibo.com/123, weibo.com/123/profile.
with cand as (
    select a.id,
           (regexp_match(a.handle::text, 'weibo\.(?:com|cn)/(?:u/)?(\d{6,})'))[1] as uid,
           row_number() over (
               partition by (regexp_match(a.handle::text, 'weibo\.(?:com|cn)/(?:u/)?(\d{6,})'))[1]
               order by a.id) as rn
    from accounts a
    where a.platform_id = (select id from platforms where slug = 'website')
      and a.handle::text ~* 'weibo\.(?:com|cn)/(?:u/)?\d{6,}'
)
update accounts a
set platform_id = (select id from platforms where slug = 'weibo'),
    native_id = cand.uid,
    handle = cand.uid,
    profile_url = 'https://weibo.com/u/' || cand.uid
from cand
where cand.id = a.id and cand.rn = 1
  and not exists (
      select 1 from accounts b
      where b.platform_id = (select id from platforms where slug = 'weibo')
        and (b.native_id = cand.uid or b.handle = cand.uid::citext)
  );

-- Weibo vanity-name form: weibo.com/somename.
with cand as (
    select a.id,
           (regexp_match(a.handle::text, 'weibo\.(?:com|cn)/([A-Za-z][A-Za-z0-9_-]{2,29})'))[1] as h,
           row_number() over (
               partition by lower((regexp_match(a.handle::text, 'weibo\.(?:com|cn)/([A-Za-z][A-Za-z0-9_-]{2,29})'))[1])
               order by a.id) as rn
    from accounts a
    where a.platform_id = (select id from platforms where slug = 'website')
      and a.handle::text ~* 'weibo\.(?:com|cn)/[A-Za-z][A-Za-z0-9_-]{2,29}'
      and a.handle::text !~* 'weibo\.(?:com|cn)/(?:u/|n/|p/|tv/|hot/|search|login|signup)'
)
update accounts a
set platform_id = (select id from platforms where slug = 'weibo'),
    handle = cand.h,
    profile_url = 'https://weibo.com/' || cand.h
from cand
where cand.id = a.id and cand.rn = 1
  and not exists (
      select 1 from accounts b
      where b.platform_id = (select id from platforms where slug = 'weibo')
        and b.handle = cand.h::citext
  );

-- Facebook vanity handles: facebook.com/somename (min 5 chars, [A-Za-z0-9.]).
with cand as (
    select a.id,
           (regexp_match(a.handle::text, 'facebook\.com/([A-Za-z0-9.]{5,50})'))[1] as h,
           row_number() over (
               partition by lower((regexp_match(a.handle::text, 'facebook\.com/([A-Za-z0-9.]{5,50})'))[1])
               order by a.id) as rn
    from accounts a
    where a.platform_id = (select id from platforms where slug = 'website')
      and a.handle::text ~* 'facebook\.com/[A-Za-z0-9.]{5,50}'
      and a.handle::text !~* 'facebook\.com/(?:profile\.php|sharer?|groups|pages|events|watch|marketplace|photos?|permalink|story|hashtag|login|people|reel|gaming|help|policies)'
)
update accounts a
set platform_id = (select id from platforms where slug = 'facebook'),
    handle = cand.h,
    profile_url = 'https://www.facebook.com/' || cand.h
from cand
where cand.id = a.id and cand.rn = 1
  and not exists (
      select 1 from accounts b
      where b.platform_id = (select id from platforms where slug = 'facebook')
        and b.handle = cand.h::citext
  );
