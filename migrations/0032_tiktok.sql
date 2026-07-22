-- TikTok as a first-class identity platform. Display-only like Instagram /
-- Weibo / Facebook: never fetched (no compliant harvest), but the handle an
-- artist publishes is an identity claim worth showing and clustering on.
-- Previously tiktok.com/@… links fell through to the generic 'website'
-- catch-all (158 rows at migration time).

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('tiktok', 'TikTok', 'social', true, 'https://www.tiktok.com/@{handle}',
     'DISPLAY ONLY: never fetched; @handles only. vm/vt.tiktok.com share links resolve as shorteners first.')
on conflict (slug) do nothing;

update platforms set display_rank = 10 where slug = 'tiktok';

-- Reclassify existing accounts misfiled under 'website'. Same collision-safe
-- pattern as migrations 0018/0020: skip handles already taken on the new
-- platform; re-extraction heals those edges on the next pass.
with cand as (
    select a.id,
           (regexp_match(a.handle::text, 'tiktok\.com/@([A-Za-z0-9_.]{2,24})'))[1] as tk_handle,
           row_number() over (
               partition by lower((regexp_match(a.handle::text, 'tiktok\.com/@([A-Za-z0-9_.]{2,24})'))[1])
               order by a.id) as rn
    from accounts a
    where a.platform_id = (select id from platforms where slug = 'website')
      and a.handle::text ~* 'tiktok\.com/@[A-Za-z0-9_.]{2,24}'
)
update accounts a
set platform_id = (select id from platforms where slug = 'tiktok'),
    handle = cand.tk_handle,
    profile_url = 'https://www.tiktok.com/@' || cand.tk_handle
from cand
where a.id = cand.id and cand.rn = 1
  and not exists (select 1 from accounts b
                  where b.platform_id = (select id from platforms where slug = 'tiktok')
                    and b.handle = cand.tk_handle::citext);

-- Edges from these accounts were 'related (website)' claims; the reextract
-- pass after this migration re-emits them as same_person tiktok links.
