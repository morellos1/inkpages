-- Add the vgen family to directory_entries.sources so the
-- directory's source facet can filter on it (discovered_via
-- 'vgen_marketplace' -> 'vgen'). View body otherwise identical
-- to migration 0030's (dumped via pg_get_viewdef).

create or replace view directory_entries as
SELECT id AS artist_id,
    public_slug,
    COALESCE(( SELECT COALESCE(NULLIF(ac.display_name, ''::text), (ac.handle)::text) AS "coalesce"
           FROM ((artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
             JOIN platforms p ON ((p.id = ac.platform_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id))))))
          ORDER BY p.display_rank, ac.followers_count DESC NULLS LAST, p.slug, ac.handle
         LIMIT 1), display_name) AS display_name,
    region,
    (EXISTS ( SELECT 1
           FROM ((artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
             JOIN attestations att ON (((att.account_id = aa.account_id) AND att.active)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id)))))))) AS no_ai_attested,
    ( SELECT jsonb_agg(jsonb_build_object('platform', p.slug, 'handle', ac.handle, 'display_name', ac.display_name, 'url', ac.profile_url, 'confidence', aa.confidence, 'last_post_at', ac.last_post_at, 'contact_email', ac.contact_email, 'commission_status', NULLIF(ac.commission_status, 'unknown'::text), 'stats', ac.platform_stats, 'avatar_url', ac.avatar_url) ORDER BY p.display_rank, ac.followers_count DESC NULLS LAST, p.slug, ac.handle) AS jsonb_agg
           FROM ((artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
             JOIN platforms p ON ((p.id = ac.platform_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id))))))) AS accounts,
    (EXISTS ( SELECT 1
           FROM ((artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
             JOIN content_flags cf ON (((cf.account_id = aa.account_id) AND cf.active AND (cf.flag = 'nsfw'::text))))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id)))))))) AS nsfw,
    ( SELECT max(ac.last_post_at) AS max
           FROM (artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id))))))) AS last_active_at,
    COALESCE((( SELECT max(ac.last_post_at) AS max
           FROM (artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id))))))) < (now() - '180 days'::interval)), false) AS dormant,
    ( SELECT
                CASE
                    WHEN (count(*) > 0) THEN jsonb_build_object('skeb_open', bool_or(((p.slug = 'skeb'::text) AND (c.commission_status = 'open'::text) AND (c.commission_detail ~~ 'skeb:%'::text))), 'pixiv_open', bool_or(((p.slug = 'pixiv'::text) AND (c.commission_status = 'open'::text) AND (c.commission_detail ~~ 'pixiv:%'::text))), 'bio_status', ( SELECT c2.commission_status
                       FROM (artist_accounts aa2
                         JOIN accounts c2 ON ((c2.id = aa2.account_id)))
                      WHERE ((aa2.artist_id = a.id) AND (aa2.removed_at IS NULL) AND (c2.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                               FROM suppressions s
                              WHERE ((s.lifted_at IS NULL) AND (s.account_id = c2.id))))) AND (c2.commission_status <> 'unknown'::text) AND (COALESCE(c2.commission_detail, ''::text) !~~ 'skeb:%'::text) AND (COALESCE(c2.commission_detail, ''::text) !~~ 'pixiv:%'::text))
                      ORDER BY c2.commission_checked_at DESC NULLS LAST, c2.commission_confidence DESC NULLS LAST
                     LIMIT 1), 'checked_at', max(c.commission_checked_at))
                    ELSE NULL::jsonb
                END AS "case"
           FROM ((artist_accounts aa
             JOIN accounts c ON ((c.id = aa.account_id)))
             JOIN platforms p ON ((p.id = c.platform_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (c.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = c.id))))) AND (c.commission_status <> 'unknown'::text))) AS commissions,
    language,
    ( SELECT ac.avatar_url
           FROM ((artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
             JOIN platforms p ON ((p.id = ac.platform_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id))))) AND (ac.avatar_url IS NOT NULL))
          ORDER BY p.display_rank, ac.followers_count DESC NULLS LAST, p.slug
         LIMIT 1) AS avatar_url,
    ( SELECT array_remove(array_agg(DISTINCT
                CASE
                    WHEN (ac.discovered_via = 'skeb_ranking'::text) THEN 'skeb'::text
                    WHEN (ac.discovered_via ~~ 'bsky_%'::text) THEN 'bluesky'::text
                    WHEN (ac.discovered_via = ANY (ARRAY['portfolioday'::text, 'portfolioday_mention'::text, 'twitter_list'::text])) THEN 'twitter'::text
                    WHEN (ac.discovered_via = ANY (ARRAY['pixiv_ranking'::text, 'pixiv_tag_search'::text])) THEN 'pixiv'::text
                    WHEN (ac.discovered_via = 'patreon_ranking'::text) THEN 'patreon'::text
                    WHEN (ac.discovered_via = 'artstation_ranking'::text) THEN 'artstation'::text
                    WHEN (ac.discovered_via = 'deviantart_popular'::text) THEN 'deviantart'::text
                    WHEN (ac.discovered_via = 'vgen_marketplace'::text) THEN 'vgen'::text
                    ELSE NULL::text
                END), NULL::text) AS array_remove
           FROM (artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id))))))) AS sources,
    COALESCE(( SELECT max(sn.captured_at) AS max
           FROM (((artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
             JOIN platforms p ON ((p.id = ac.platform_id)))
             JOIN account_snapshots sn ON ((sn.account_id = ac.id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (p.slug = 'twitter'::text) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id))))))), ( SELECT max(sn.captured_at) AS max
           FROM ((artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
             JOIN account_snapshots sn ON ((sn.account_id = ac.id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id)))))))) AS hydrated_at
   FROM artists a
  WHERE ((status = 'active'::text) AND (merged_into IS NULL) AND (EXISTS ( SELECT 1
           FROM (artist_accounts aa
             JOIN accounts ac ON ((ac.id = aa.account_id)))
          WHERE ((aa.artist_id = a.id) AND (aa.removed_at IS NULL) AND (ac.status = ANY (ARRAY['active'::text, 'unknown'::text])) AND (NOT (EXISTS ( SELECT 1
                   FROM suppressions s
                  WHERE ((s.lifted_at IS NULL) AND (s.account_id = ac.id)))))))) AND (NOT (EXISTS ( SELECT 1
           FROM suppressions s
          WHERE ((s.lifted_at IS NULL) AND (s.artist_id = a.id))))));
