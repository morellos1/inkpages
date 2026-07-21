-- Platform seed rows, including per-platform fetch policy.

insert into platforms (slug, display_name, kind, display_only, profile_url_template, notes) values
    ('twitter',    'Twitter/X',  'social',     false, 'https://x.com/{handle}',                   'Official pay-per-use API only (~$0.01/user read, 2M post reads/mo cap). Never scraped.'),
    ('bluesky',    'Bluesky',    'social',     false, 'https://bsky.app/profile/{handle}',        'Open AT Protocol; getProfiles batches of 25, free.'),
    ('pixiv',      'Pixiv',      'portfolio',  false, 'https://www.pixiv.net/users/{native_id}',  null),
    ('skeb',       'Skeb',       'commission', false, 'https://skeb.jp/@{handle}',                'Creator rankings display X handles (artist-published).'),
    ('artstation', 'ArtStation', 'portfolio',  false, 'https://www.artstation.com/{handle}',      null),
    ('patreon',    'Patreon',    'support',    false, 'https://www.patreon.com/{handle}',         null),
    ('vgen',       'VGen',       'commission', false, 'https://vgen.co/{handle}',                 null),
    ('cara',       'Cara',       'portfolio',  false, 'https://cara.app/{handle}',                'No-AI platform: membership is an attestation signal.'),
    ('xfolio',     'XFolio',     'portfolio',  false, 'https://xfolio.jp/portfolio/{handle}',     'No-AI policy: membership is an attestation signal.'),
    ('kofi',       'Ko-fi',      'support',    false, 'https://ko-fi.com/{handle}',               null),
    ('deviantart', 'DeviantArt', 'portfolio',  false, 'https://www.deviantart.com/{handle}',      'Official API; per-work noai flags.'),
    ('tumblr',     'Tumblr',     'social',     false, 'https://{handle}.tumblr.com',              'Public API.'),
    ('gumroad',    'Gumroad',    'support',    false, 'https://{handle}.gumroad.com',             null),
    ('inprnt',     'INPRNT',     'support',    false, 'https://www.inprnt.com/gallery/{handle}/', null),
    ('instagram',  'Instagram',  'social',     true,  'https://www.instagram.com/{handle}',       'DISPLAY ONLY: never fetched or crawled; handles harvested from bios elsewhere, shown unlinked.'),
    ('linktree',   'Linktree',   'link_hub',   false, 'https://linktr.ee/{handle}',               null),
    ('carrd',      'Carrd',      'link_hub',   false, 'https://{handle}.carrd.co',                null),
    ('potofu',     'potofu.me',  'link_hub',   false, 'https://potofu.me/{handle}',               null),
    ('litlink',    'lit.link',   'link_hub',   false, 'https://lit.link/{handle}',                null);
