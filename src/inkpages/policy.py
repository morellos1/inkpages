"""Pipeline policy constants."""

# Sources that put an account on an artist roster by construction (art feeds,
# creator rankings, curated lists, the #PortfolioDay harvest). Accounts
# discovered via these become singleton artists at clustering time even with
# zero identity edges — this is what keeps high-follower, Twitter-only artists
# with no external links in the directory. Accounts discovered only
# incidentally (e.g. as a bio_link target) need an edge into a cluster or a
# human decision before an artist exists for them.
# One-directional attaches whose *target* has at least this many followers go
# to human review instead of auto-attaching: impersonators link to famous
# accounts, so that is exactly where a lone bio link stops being sufficient.
REVIEW_FOLLOWER_THRESHOLD = 10_000

# Reciprocity rescue: a one-directional link to a *prominent* account normally
# flips to a `related` connection (impersonation guard). But if that target also
# links back to at least this many of the source artist's OWN distinctive
# downstream targets (personal hubs like a specific Carrd/Patreon — not
# community resources, not other prominent accounts), the two are provably the
# same person and the target attaches instead. Two coincident personal hubs
# between unrelated artists is vanishingly unlikely, which is the guard.
RECIPROCITY_SHARED_MIN = 2

# No artist may accumulate more than this many accounts on one identity
# platform via clustering — nobody has 12 Twitters; a growing pile of
# same-platform accounts means a chain reaction through shared/project links.
# Attaches beyond the cap go to review; components beyond it never auto-merge.
MAX_SAME_PLATFORM = 3

# Anomaly flags for manual review — shapes that suggest a credits/projects
# page rather than personal links (e.g. a VTuber illustrator's lit.link that
# lists every client they've drawn for).
# Raised 12/10 → 25/20 (2026-07-23, user directive): at directory scale the
# old bounds flagged plenty of legitimately link-rich personal hubs and the
# queue outgrew hand review. Migration 0034 retro-resolved pending items that
# only qualified under the old bounds.
ANOMALY_HUB_FANOUT = 25           # one member hub linking this many accounts
ANOMALY_HUB_ATTACHED = 20         # members that exist only because a hub listed
                                  # them (5-7 is a normal personal linktree)
ANOMALY_RELATED_CONNECTIONS = 15  # sheer connection volume
ANOMALY_CROSS_ARTIST_REFS = 3     # a MEMBER account with edges touching this
                                  # many OTHER artists — either a junk shared
                                  # target that slipped in (tumblr.com/contact)
                                  # or several unmerged alts of one person

# Standing verification cull: twitter/bluesky accounts under this many
# followers are set status='hidden' (directory-invisible, data kept) on every
# pipeline run. Originally a one-time migration-0016 action — accounts
# hydrated afterwards were slipping back in. Reversible per account from the
# review UI's Removed page.
CULL_MIN_FOLLOWERS = 50

# No post/repost within this window => the account is labeled dormant.
# Mirrored in the directory_entries view (migration 0006) — keep in sync.
DORMANT_AFTER_DAYS = 180

# Open-harvest sources: anyone can post a hashtag, so accounts from these
# need artist evidence (art-keyword bio or outbound platform links) before a
# singleton artist is created. Curated rosters (art feeds, creator rankings,
# lists) are exempt — the source itself is the evidence.
HARVEST_NEEDS_EVIDENCE = {"portfolioday", "portfolioday_mention"}

ROSTER_SOURCES = {
    "bsky_feed",
    "bsky_starter_pack",
    "bsky_list",
    "portfolioday",
    "portfolioday_mention",
    "twitter_list",
    "skeb_ranking",
    "pixiv_ranking",
    # Popularity-sorted tag search (premium popular_d, AI-flagged works
    # excluded) — appearing there is roster-grade evidence of being an artist,
    # exactly like charting in a ranking.
    "pixiv_tag_search",
    "patreon_ranking",
    # Charting on ArtStation's community trending feed — roster-grade like
    # a pixiv ranking; profiles are bot-walled so these arrive with no edges.
    "artstation_ranking",
    # Charting on DeviantArt's Popular RSS feed (official public backend).
    "deviantart_popular",
    # Surfaced at the head of a VGen marketplace category listing.
    "vgen_marketplace",
    "convention_list",
    "platform_roster",  # Cara / ArtStation / DeviantArt / XFolio enumerations
    # One-click tags from the x-tag browser extension: a human looked at the
    # profile and said "artist" — the strongest roster evidence there is.
    "manual_tag",
}
