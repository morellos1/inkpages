-- First-class platforms for profile hosts that were collapsing into shared
-- 'website' accounts (review feedback follow-up), plus the bio.site link hub.

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('furaffinity', 'Fur Affinity', 'portfolio', false, 'https://www.furaffinity.net/user/{handle}', null),
    ('behance',     'Behance',      'portfolio', false, 'https://www.behance.net/{handle}',          null),
    ('boosty',      'Boosty',       'support',   false, 'https://boosty.to/{handle}',                null),
    ('artfight',    'Art Fight',    'portfolio', false, 'https://artfight.net/~{handle}',            null),
    ('biosite',     'Bio Site',     'link_hub',  false, 'https://bio.site/{handle}',                 'Squarespace link hub');
