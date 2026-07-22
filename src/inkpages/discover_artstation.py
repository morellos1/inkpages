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
import json
import time
from collections import Counter

import httpx

from . import db
from .crawl_links import UA
from .extract import (find_attestations, find_nsfw_flags,
                      find_platform_links, find_website_links)

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


WB_AVAILABLE = "https://archive.org/wayback/available"
# Dedicated social-URL fields on the profile JSON (the social_profiles array
# mostly duplicates these; both are scanned, edges dedupe).
_URL_FIELDS = ("twitter_url", "instagram_url", "tumblr_url", "twitch_url",
               "youtube_url", "deviantart_url", "behance_url", "website_url",
               "pinterest_url", "sketchfab_url", "vimeo_url")


def _merge_stats(conn, account_id: int, patch: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """update accounts
               set platform_stats = coalesce(platform_stats, '{}'::jsonb) || %s
               where id = %s""", (json.dumps(patch), account_id))


def wayback_enrich(conn, client, platforms, limit, stats) -> None:
    """ArtStation bot-walls its live profiles, but the Internet Archive holds
    organically captured copies of many users/{u}.json responses — the
    artist's own published profile, read from a public archive without
    touching ArtStation's wall. (We deliberately do NOT mass-request fresh
    Save-Page-Now captures: that would just proxy-fetch around their bot
    protection.) Coverage skews to well-known artists; misses are remembered.

    Hits yield the full profile: social URLs -> profile_field same_person
    edges (user-entered, normal guards), headline -> bio extraction,
    follower count, stable id. Evidence carries the archive timestamp."""
    with conn.cursor() as cur:
        cur.execute(
            """select a.id, a.handle::text from accounts a
               join platforms p on p.id = a.platform_id
               where p.slug = 'artstation' and a.status not in ('deleted', 'hidden')
                 and not (coalesce(a.platform_stats, '{}'::jsonb) ? 'wayback_checked')
               order by exists (select 1 from artist_accounts aa
                                where aa.account_id = a.id
                                  and aa.removed_at is null) desc, a.id
               limit %s""", (limit,))
        todo = cur.fetchall()
    print(f"wayback enrich: {len(todo)} artstation accounts to check")
    for n, (account_id, handle) in enumerate(todo, 1):
        time.sleep(1.0)
        try:
            avail = client.get(WB_AVAILABLE, params={
                "url": f"artstation.com/users/{handle}.json"}, timeout=20).json()
        except (httpx.HTTPError, ValueError):
            stats["wb_avail_failed"] += 1
            continue
        closest = (avail.get("archived_snapshots") or {}).get("closest") or {}
        if not closest.get("available"):
            _merge_stats(conn, account_id, {"wayback_checked": True,
                                            "wayback_archived": False})
            stats["wb_not_archived"] += 1
            continue
        ts = closest["timestamp"]
        archive_url = (f"https://web.archive.org/web/{ts}id_/"
                       f"https://www.artstation.com/users/{handle}.json")
        try:
            data = client.get(archive_url, timeout=30,
                              follow_redirects=True).json()
        except (httpx.HTTPError, ValueError):
            stats["wb_fetch_failed"] += 1  # not marked checked — retried next run
            continue

        bio = data.get("headline") or ""
        account_id = db.get_or_create_account(
            conn, platforms["artstation"],
            native_id=str(data["id"]) if data.get("id") else None,
            handle=data.get("username") or handle,
            display_name=data.get("full_name"),
            profile_url=f"https://www.artstation.com/{handle}",
            followers_count=data.get("followers_count"),
            discovered_via="bio_link",  # first discovery wins anyway
            hydrated=True,
        )
        snapshot_id = db.insert_snapshot(
            conn, account_id, bio_text=bio or None,
            display_name=data.get("full_name"),
            followers_count=data.get("followers_count"), following_count=None,
            raw={"wayback_timestamp": ts, "archive_url": archive_url,
                 "social_profiles": data.get("social_profiles"),
                 **{k: data.get(k) for k in _URL_FIELDS + ("headline", "city",
                                                           "country", "skills")}},
            fetch_source="artstation:wayback",
        )
        _merge_stats(conn, account_id, {"wayback_checked": True,
                                        "wayback_archived": ts[:8]})
        with conn.cursor() as cur:
            # The archived avatar URL is stale-but-real; only fill a gap.
            cur.execute("""update accounts set avatar_url = coalesce(avatar_url, %s)
                           where id = %s""",
                        (data.get("medium_avatar_url"), account_id))
        for signal, matched in find_attestations(bio):
            db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
        for signal, matched in find_nsfw_flags(bio):
            db.upsert_content_flag(conn, account_id, "nsfw", signal, matched,
                                   snapshot_id)

        urls = [data.get(k) for k in _URL_FIELDS]
        urls += [s.get("url") for s in data.get("social_profiles") or []
                 if s.get("social_network") != "public_email"]
        emitted: set[int] = set()
        for url in dict.fromkeys(u for u in urls if u and "*" not in u):
            for link in find_platform_links(url) + find_website_links(url):
                platform_id = platforms.get(link.platform)
                if platform_id is None or link.platform == "artstation":
                    continue
                target_id = db.get_or_create_account(
                    conn, platform_id, native_id=link.native_id,
                    handle=link.handle or link.native_id, profile_url=link.url,
                    discovered_via="bio_link",
                    discovery_details={"source_account_id": account_id,
                                       "via": "artstation_wayback"})
                if target_id == account_id or target_id in emitted:
                    continue
                emitted.add(target_id)
                claim = "related" if link.platform == "website" else "same_person"
                db.upsert_edge(conn, account_id, target_id,
                               evidence_type="profile_field",
                               evidence_snapshot_id=snapshot_id,
                               evidence_url=link.url,
                               matched_text=f"archived {ts[:8]}",
                               claim=claim,
                               relation_hint="website" if claim == "related" else None)
                stats["wb_edges"] += 1
        stats["wb_enriched"] += 1
        if n % 25 == 0:
            conn.commit()
            print(f"  …{n} checked ({stats['wb_enriched']} archived)")
    db.log_api_usage(conn, "wayback", "artstation-users", len(todo), 0)
    conn.commit()


def enrich_known(conn, client, platforms, stats) -> None:
    """Cross-hydration rule, within what the open surface allows: sweep every
    trending dimension at full depth and refresh ONLY accounts already in the
    db (bio-link targets predating this source) with their stable id, display
    name and avatar. Creates nothing. Accounts that never chart have no
    fetchable surface — ArtStation bot-walls profiles — and stay as the
    handle-only rows their bio links minted."""
    with conn.cursor() as cur:
        cur.execute(
            """select lower(handle::text), id from accounts
               where platform_id = %s""", (platforms["artstation"],))
        known = dict(cur.fetchall())
    seen: set[str] = set()
    for dimension in ("2d", "3d", "all"):
        for page in range(1, MAX_PAGES_PER_DIMENSION + 1):
            items = fetch_trending(client, dimension, page)
            stats["feed_pages"] += 1
            if not items:
                break
            for pos, item in enumerate(items, (page - 1) * 100 + 1):
                user = item.get("user") or {}
                username = (user.get("username") or "").lower()
                if username not in known or username in seen:
                    continue
                seen.add(username)
                account_id = db.get_or_create_account(
                    conn, platforms["artstation"],
                    native_id=str(user["id"]),
                    handle=user["username"],
                    display_name=user.get("full_name"),
                    profile_url=f"https://www.artstation.com/{user['username']}",
                    discovered_via="bio_link",  # first discovery wins anyway
                    hydrated=True,
                )
                db.set_avatar(conn, account_id, user.get("medium_avatar_url"))
                db.set_platform_stats(conn, account_id, {
                    "artstation_dimension": dimension,
                    "artstation_position": pos,
                    "pro_member": bool(user.get("pro_member")),
                })
                stats["enriched_known"] += 1
            time.sleep(0.8)
    db.log_api_usage(conn, "artstation", "community/trending(enrich)",
                     stats["feed_pages"], 0)
    conn.commit()
    print(f"enriched {stats['enriched_known']} of {len(known)} known accounts "
          f"({len(known) - stats['enriched_known']} never charted — nothing "
          "fetchable for them)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-new", type=int, default=500,
                        help="stop after this many creators not already in "
                             "the db (0 = take everything the feed pages out)")
    parser.add_argument("--enrich-known", action="store_true",
                        help="full-depth trending sweep that refreshes only "
                             "accounts already in the db; creates nothing")
    parser.add_argument("--wayback-enrich", action="store_true",
                        help="pull archived users/{u}.json profiles from the "
                             "Internet Archive (social links, followers, bio)")
    parser.add_argument("--limit", type=int, default=700,
                        help="max accounts per wayback-enrich run")
    args = parser.parse_args()

    stats: Counter = Counter()
    with httpx.Client(headers={**UA, "Accept": "application/json"},
                      follow_redirects=True) as client, db.connect() as conn:
        platforms = db.platform_ids(conn)
        if args.enrich_known or args.wayback_enrich:
            if args.enrich_known:
                enrich_known(conn, client, platforms, stats)
            if args.wayback_enrich:
                wayback_enrich(conn, client, platforms, args.limit, stats)
            print("done:", dict(stats))
            return
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
