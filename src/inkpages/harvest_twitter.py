"""Bounded #PortfolioDay-style harvest via the paid X search API.

Post reads are the cost driver (~$0.005 each); author profiles ride along on
the author_id expansion for free, so harvest and hydration are one pass.
The budget guard aborts before any paid call if the cap would be exceeded.

Usage:
  uv run python -m inkpages.harvest_twitter --max-posts 1000 --top 300
"""
import argparse
import math
from collections import Counter

from . import db
from .twitter import POST_READ_CENTS, XApi, ensure_budget, process_user


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="#PortfolioDay -is:retweet")
    parser.add_argument("--max-posts", type=int, default=1000)
    parser.add_argument("--top", type=int, default=300,
                        help="keep only the N harvested authors with the most followers")
    args = parser.parse_args()

    stats: Counter = Counter()
    api = XApi()
    with db.connect() as conn:
        ensure_budget(conn, args.max_posts * POST_READ_CENTS)
        platforms = db.platform_ids(conn)

        posts_read, users = api.search_recent(args.query, args.max_posts)
        db.log_api_usage(conn, "x_api", "tweets/search/recent", posts_read,
                         math.ceil(posts_read * POST_READ_CENTS),
                         note=f"query={args.query!r}")
        conn.commit()
        print(f"read {posts_read} posts -> {len(users)} unique authors")

        authors = sorted(users.values(),
                         key=lambda u: (u.get("public_metrics") or {}).get("followers_count", 0),
                         reverse=True)[:args.top]
        for user in authors:
            process_user(conn, platforms, user, "portfolioday",
                         {"query": args.query}, stats)
        conn.commit()

    print("done:", dict(stats))


if __name__ == "__main__":
    main()
