-- Additional tracked platforms (review feedback 2026-07-20) + the generic
-- personal-website platform (handle = domain).

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('mihuashi', '米画师 Mihuashi', 'commission', false, 'https://www.mihuashi.com/users/{handle}', 'CN commission platform'),
    ('youtube',  'YouTube',        'social',     false, 'https://www.youtube.com/@{handle}',      null),
    ('discord',  'Discord',        'social',     false, null,                                     'Server invites; codes can expire'),
    ('telegram', 'Telegram',       'social',     false, 'https://t.me/{handle}',                  null),
    ('twitch',   'Twitch',         'social',     false, 'https://www.twitch.tv/{handle}',         null),
    ('website',  'Personal site',  'portfolio',  false, null,                                     'Generic fallback: handle = domain');
