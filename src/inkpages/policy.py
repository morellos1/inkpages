"""Pipeline policy constants."""

# Sources that put an account on an artist roster by construction (art feeds,
# creator rankings, curated lists, the #PortfolioDay harvest). Accounts
# discovered via these become singleton artists at clustering time even with
# zero identity edges — this is what keeps high-follower, Twitter-only artists
# with no external links in the directory. Accounts discovered only
# incidentally (e.g. as a bio_link target) need an edge into a cluster or a
# human decision before an artist exists for them.
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
