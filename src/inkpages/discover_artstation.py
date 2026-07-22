"""ArtStation discovery via the community trending feed (free).

ArtStation's robots.txt permits /api/v2 (their own sitemaps live there), and
the community explore endpoints serve JSON to plain clients. Everything
profile-shaped (users/{u}.json, project detail, HTML pages) sits behind a
Cloudflare bot challenge — we do NOT engineer around bot protection, so this
worker takes only what the open surface offers:

- trending project feed (dimension=2d for illustrators, then all), paged at
  100/feed-page -> distinct users become roster accounts at
  discovered_via='artstation_ranking' (charting on trending is roster-grade
  evidence, like a pixiv ranking).
- each item carries the user's stable numeric id, username, display name and
  avatar. No bios, no follower counts, no social links — cross-platform
  edges form from the OTHER side (artists' twitter/pixiv/hub bios linking
  artstation.com/username), which get_or_create unifies by handle.

Usage: uv run python -m inkpages.discover_artstation --max-new 500
"""
import argparse
import time
from collections import Counter

import httpx

from . import db
from .crawl_links import UA

TRENDING = "https://www.artstation.com/api/v2/community/explore/projects/trending.json"
# 2d first: illustration/concept art is the directory's demographic; 'all'
# tops up with the site-wide list.
DIMENSIONS = ("2d", "all")
MAX_PAGES_PER_DIMENSION = 30


def fetch_trending(client, dimension: str, page: int) -> list[dict]:
    try:
        resp = client.get(TRENDING, params={
            "page": page, "dimension": dimension, "per_page": 100})
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    try:
        return resp.json().get("data") or []
    except ValueError:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-new", type=int, default=500,
                        help="stop after this many creators not already in "
                             "the db (0 = take everything the feed pages out)")
    args = parser.parse_args()

    stats: Counter = Counter()
    with httpx.Client(headers={**UA, "Accept": "application/json"},
                      follow_redirects=True) as client, db.connect() as conn:
        platforms = db.platform_ids(conn)
        with conn.cursor() as cur:
            cur.execute(
                """select coalesce(native_id, handle::text) from accounts
                   where platform_id = %s""", (platforms["artstation"],))
            known_ids = {r[0] for r in cur.fetchall()}
            cur.execute(
                """select lower(handle::text) from accounts
                   where platform_id = %s""", (platforms["artstation"],))
            known_handles = {r[0] for r in cur.fetchall()}

        # position = first-seen order in the highest-priority dimension; the
        # trending feed IS the ranking.
        creators: dict[int, dict] = {}
        new_count = 0
        for dimension in DIMENSIONS:
            if args.max_new and new_count >= args.max_new:
                break
            for page in range(1, MAX_PAGES_PER_DIMENSION + 1):
                items = fetch_trending(client, dimension, page)
                stats["feed_pages"] += 1
                if not items:
                    break
                for item in items:
                    user = item.get("user") or {}
                    uid, username = user.get("id"), user.get("username")
                    if not uid or not username or uid in creators:
                        continue
                    is_new = (str(uid) not in known_ids
                              and username.lower() not in known_handles)
                    if is_new and args.max_new and new_count >= args.max_new:
                        continue
                    creators[uid] = {
                        "id": uid, "username": username,
                        "full_name": user.get("full_name"),
                        "avatar": user.get("medium_avatar_url"),
                        "pro": bool(user.get("pro_member")),
                        "dimension": dimension,
                        "position": len(creators) + 1,
                        "sample": {"title": item.get("title"),
                                   "url": item.get("url")},
                    }
                    new_count += is_new
                if args.max_new and new_count >= args.max_new:
                    break
                time.sleep(0.8)
        db.log_api_usage(conn, "artstation", "community/trending",
                         stats["feed_pages"], 0)
        print(f"artstation trending: {len(creators)} distinct creators "
              f"({new_count} new) across {stats['feed_pages']} feed pages")

        for row in creators.values():
            account_id = db.get_or_create_account(
                conn, platforms["artstation"],
                native_id=str(row["id"]),
                handle=row["username"],
                display_name=row["full_name"],
                profile_url=f"https://www.artstation.com/{row['username']}",
                discovered_via="artstation_ranking",
                discovery_details={"dimension": row["dimension"],
                                   "position": row["position"]},
                hydrated=True,  # nothing more is fetchable (profiles bot-walled)
            )
            stats["accounts"] += 1
            if db.is_suppressed(conn, account_id):
                stats["skipped_suppressed"] += 1
                continue
            db.set_avatar(conn, account_id, row["avatar"])
            db.set_platform_stats(conn, account_id, {
                "artstation_dimension": row["dimension"],
                "artstation_position": row["position"],
                "pro_member": row["pro"],
            })
            db.insert_snapshot(
                conn, account_id, bio_text=None,
                display_name=row["full_name"], followers_count=None,
                following_count=None,
                raw={"trending": {k: row[k] for k in
                                  ("dimension", "position", "sample", "pro")}},
                fetch_source="artstation:trending",
            )
            stats["snapshots"] += 1
        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
