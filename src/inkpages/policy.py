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
    "patreon_ranking",
    "convention_list",
    "platform_roster",  # Cara / ArtStation / DeviantArt / XFolio enumerations
}
