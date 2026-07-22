"""VGen discovery via the marketplace's server-rendered category listings
(free) + hydration from the artist's own profile page.

VGen's robots.txt allows generic agents (Cloudflare Content-Signal
`search=yes, use=reference` — a reference directory is the permitted use);
every page is server-rendered Next.js with a full JSON payload. There is no
public "top sellers" sort, so rank is derived: the searchCategories sitemap
lists ~1,050 category/subject/style pages, each carrying its top-20
relevance-ranked services with the artist's review stats inline — one pass
over all of them, then artists rank by their best totalReviews.

Stage 1 (--harvest --top N): walk every category listing, aggregate per
artist (best reviews, categories seen, mature flags), mint the top N as
discovered_via='vgen_marketplace' (roster-grade: surfaced at the head of a
marketplace niche). A mature-flagged surfaced service => nsfw platform_flag.

Stage 2 (--hydrate-known): fetch vgen.co/{username} for vgen accounts never
hydrated — marketplace arrivals and bio-link targets alike (cross-hydration
rule). __NEXT_DATA__ carries userID (stable native_id), the registered
socials list (user-entered => profile_field edges, no oauth exemption), bio,
avatar, languages, per-service tags, and servicesStatus OPEN/CLOSED —
platform-authoritative commission state (detail 'vgen:services_status').
Unlike deviantart:about, the stored bio_text IS the full extraction source,
so reextract owns vgen bio_link edges normally — no exclusion needed.

Usage:
  uv run python -m inkpages.discover_vgen --harvest --top 1000
  uv run python -m inkpages.discover_vgen --hydrate-known --limit 1200
"""
import argparse
import json
import re
import time
from collections import Counter

import httpx

from . import db
from .crawl_links import UA, fetch_page
from .extract import (find_attestations, find_commission_status, find_email,
                      find_nsfw_flags, find_platform_links, find_website_links)

SITE = "https://vgen.co"
_SITEMAP_LOC = re.compile(r"<loc>([^<]+)</loc>")

# Category roots that mark a regular digital artist — tiers 1-2 of
# docs/vgen-categories.md (core illustration + vtuber/avatar/emote art).
# Harvests walk ONLY these listings unless --all-categories is passed
# (user directive 2026-07-22: tier 3-5-only artists were culled; every
# future crawl stays tier 1-2 so only artists come in).
ARTIST_ROOTS = {
    # tier 1 — core digital illustration
    "character-illustrations", "chibi-illustrations", "creature-illustrations",
    "character-reference-sheets", "creature-reference-sheets",
    "other-reference-sheets", "custom-character-design", "custom-creature-design",
    "custom-outfit-design", "custom-backgrounds", "other-custom-illustrations",
    "illustrations", "custom-comics-creation", "custom-dakimakura",
    "custom-book-cover", "custom-drawings", "other-custom-original-design",
    "original-animatics-storyboards", "digital-illustration-advice",
    # tier 2 — illustration-adjacent (vtuber/avatar/emote art)
    "vtuber-model-art", "chibi-vtuber-model-art", "creature-model-art",
    "other-2d-vtuber-model-art", "custom-pngtuber-giftuber-avatar",
    "custom-chibi-pngtuber-giftuber-avatar",
    "custom-creature-pngtuber-giftuber-avatar", "other-custom-reactive-avatars",
    "2d-avatars", "custom-emotes", "emotes-badges", "custom-chat-stickers",
    "custom-subscriber-badges", "custom-stickers", "custom-vtuber-stream-props",
    "custom-vtuber-throwables", "custom-vtuber-debut-graphics",
    "custom-vtuber-model-add-ons", "other-custom-2d-vtuber-model-add-ons",
    "custom-stream-avatar-sprites", "custom-acrylic-charms",
    "custom-posters-prints", "custom-patterns",
}


def category_root(url: str) -> str:
    tail = url.split("/category/")[-1].split("/catalogue/")[-1]
    return tail.split("/")[0]
_NEXT_DATA_MARK = "__NEXT_DATA__"


def next_data(page_html: str) -> dict | None:
    i = page_html.find(_NEXT_DATA_MARK)
    if i < 0:
        return None
    start = page_html.find(">", i) + 1
    end = page_html.find("</script>", start)
    try:
        return json.loads(page_html[start:end])
    except ValueError:
        return None


def category_urls(client) -> list[str]:
    resp = fetch_page(client, f"{SITE}/sitemap-searchCategories-1.xml")
    if resp is None or resp.status_code != 200:
        return []
    return [u for u in _SITEMAP_LOC.findall(resp.text)
            if "/category/" in u or "/catalogue/" in u]


def harvest(conn, client, platforms, stats, top: int,
            max_listings: int = 0, exclude: list[str] | None = None,
            all_categories: bool = False, max_new: int = 0) -> None:
    urls = category_urls(client)
    if not all_categories:
        urls = [u for u in urls if category_root(u) in ARTIST_ROOTS]
    if exclude:
        # Additional root-prefix skip list on top of the tier-1/2 default.
        urls = [u for u in urls
                if not any(u.split("/category/")[-1].startswith(p)
                           for p in exclude)]
    if max_listings:
        urls = urls[:max_listings]
    print(f"vgen: {len(urls)} category listings to walk")
    artists: dict[str, dict] = {}
    blocked_streak = 0
    for n, url in enumerate(urls, 1):
        time.sleep(1.5)
        resp = fetch_page(client, url)
        if resp is None or resp.status_code != 200:
            stats["list_failed"] += 1
            blocked_streak += 1
            if blocked_streak >= 5:
                print(f"  blocked at {n}/{len(urls)} — stopping harvest walk")
                stats["aborted_on_block"] = 1
                break
            continue
        blocked_streak = 0
        data = next_data(resp.text)
        services = (((data or {}).get("props", {}).get("pageProps", {})
                     .get("initialServices") or {}).get("services") or [])
        stats["lists_fetched"] += 1
        for svc in services:
            mod = svc.get("userModeration") or {}
            username = mod.get("username")
            if not username or mod.get("discoveryStatus") not in (None, "LIVE"):
                continue
            rstats = svc.get("artistReviewStats") or {}
            reviews = rstats.get("totalReviews") or 0
            row = artists.setdefault(username, {
                "username": username,
                "display_name": mod.get("displayName") or username,
                "user_id": svc.get("userID"),
                "reviews": 0, "rating": None, "mature": False,
                "categories": set(),
            })
            row["user_id"] = row["user_id"] or svc.get("userID")
            if reviews >= row["reviews"]:
                row["reviews"] = reviews
                if rstats.get("averageRating"):
                    row["rating"] = round(float(rstats["averageRating"]), 2)
            row["mature"] = row["mature"] or bool(svc.get("containsMatureContent"))
            row["categories"].add(url.rsplit("/category/", 1)[-1]
                                  .rsplit("/catalogue/", 1)[-1])
        if n % 100 == 0:
            print(f"  …{n} listings, {len(artists)} distinct artists so far")
    db.log_api_usage(conn, "vgen", "category-listings", stats["lists_fetched"], 0)
    print(f"vgen: {len(artists)} distinct artists across "
          f"{stats['lists_fetched']} listings")

    ranked = sorted(artists.values(), key=lambda r: -r["reviews"])[:top]
    if max_new:
        # Cap NEW artists minted; already-known vgen accounts always
        # refresh (stats/categories/snapshot) without counting.
        with conn.cursor() as cur:
            cur.execute(
                """select handle::text from accounts where platform_id = %s""",
                (platforms["vgen"],))
            known = {h.lower() for (h,) in cur.fetchall()}
        kept, new_seen = [], 0
        for row in ranked:
            if row["username"].lower() in known:
                kept.append(row)
            elif new_seen < max_new:
                kept.append(row)
                new_seen += 1
            else:
                stats["skipped_over_max_new"] += 1
        ranked = kept
    for rank, row in enumerate(ranked, 1):
        account_id = db.get_or_create_account(
            conn, platforms["vgen"],
            native_id=row["user_id"],
            handle=row["username"],
            display_name=row["display_name"],
            profile_url=f"{SITE}/{row['username']}",
            discovered_via="vgen_marketplace",
            discovery_details={"source": "category_listings", "rank": rank,
                               "reviews": row["reviews"]},
        )
        stats["accounts"] += 1
        if db.is_suppressed(conn, account_id):
            stats["skipped_suppressed"] += 1
            continue
        db.set_platform_stats(conn, account_id, {
            "vgen_reviews": row["reviews"],
            **({"vgen_rating": row["rating"]} if row["rating"] else {}),
            "vgen_categories": sorted(row["categories"])[:6],
        })
        snapshot_id = db.insert_snapshot(
            conn, account_id, bio_text=None, display_name=row["display_name"],
            followers_count=None, following_count=None,
            raw={"source": "category_listings", "rank": rank,
                 "reviews": row["reviews"], "mature_service": row["mature"]},
            fetch_source="vgen:listing",
        )
        if row["mature"]:
            # The artist's own mature-content flag on a surfaced service.
            db.upsert_content_flag(conn, account_id, "nsfw", "platform_flag",
                                   "vgen:mature_service", snapshot_id)
            stats["nsfw_flags"] += 1
    conn.commit()


def profile_fields(data: dict) -> dict | None:
    user = (data.get("props", {}).get("pageProps", {}) or {}).get("user")
    if not isinstance(user, dict):
        return None
    services = data["props"]["pageProps"].get("services") or []
    tags: list[str] = []
    mature = False
    for svc in services:
        for t in svc.get("tags") or []:
            if t not in tags:
                tags.append(t)
        mature = mature or bool(svc.get("containsMatureContent"))
    rstats = user.get("artistReviewStats") or {}
    return {
        "user_id": user.get("userID"),
        "username": user.get("username"),
        "display_name": user.get("displayName"),
        "reviews": rstats.get("totalReviews"),
        "rating": (round(float(rstats["averageRating"]), 2)
                   if rstats.get("averageRating") else None),
        "bio": (user.get("bio") or "").strip(),
        "avatar": user.get("avatarURL"),
        "socials": [s.get("link") for s in user.get("socials") or []
                    if isinstance(s, dict) and s.get("link")],
        "services_status": user.get("servicesStatus"),
        "languages": user.get("languages") or [],
        "tags": tags,
        "mature": mature,
    }


def hydrate_known(conn, client, platforms, limit, stats) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """select a.id, a.handle::text from accounts a
               join platforms p on p.id = a.platform_id
               where p.slug = 'vgen' and a.last_hydrated is null
                 and a.status not in ('deleted', 'hidden')
               order by a.id limit %s""",
            (limit,),
        )
        todo = cur.fetchall()
    print(f"hydrating {len(todo)} vgen profiles")
    blocked_streak = 0
    for n, (account_id, username) in enumerate(todo, 1):
        if db.is_suppressed(conn, account_id):
            stats["skipped_suppressed"] += 1
            continue
        time.sleep(1.5)
        resp = fetch_page(client, f"{SITE}/{username}")
        if resp is not None and resp.status_code in (403, 429):
            blocked_streak += 1
            if blocked_streak >= 3:
                print(f"  block holding — stopping at {n}/{len(todo)}")
                stats["aborted_on_block"] = 1
                break
            time.sleep(90.0)
            resp = fetch_page(client, f"{SITE}/{username}")
        if resp is None:
            stats["fetch_failed"] += 1
            continue
        if resp.status_code in (404, 410):
            with conn.cursor() as cur:
                cur.execute(
                    """update accounts set status = 'deleted', last_hydrated = now()
                       where id = %s""", (account_id,))
            stats["deleted"] += 1
            continue
        if resp.status_code != 200:
            stats[f"http_{resp.status_code}"] += 1
            continue
        blocked_streak = 0
        page = profile_fields(next_data(resp.text) or {})
        if page is None:
            stats["parse_failed"] += 1
            continue
        snapshot_id = db.insert_snapshot(
            conn, account_id, bio_text=page["bio"] or None,
            display_name=page["display_name"],
            followers_count=None, following_count=None,
            raw={"user_id": page["user_id"], "socials": page["socials"],
                 "services_status": page["services_status"],
                 "languages": page["languages"], "tags": page["tags"][:20]},
            fetch_source="vgen:profile",
        )
        with conn.cursor() as cur:
            cur.execute(
                """update accounts
                   set status = case when status = 'hidden' then 'hidden'
                                     else 'active' end,
                       display_name = coalesce(%s, display_name),
                       last_hydrated = now()
                   where id = %s""",
                (page["display_name"], account_id))
            if page["user_id"]:
                cur.execute(
                    """update accounts set native_id = %(nid)s
                       where id = %(id)s and native_id is null
                         and not exists (select 1 from accounts
                                         where platform_id = %(pf)s
                                           and native_id = %(nid)s)""",
                    {"nid": page["user_id"], "id": account_id,
                     "pf": platforms["vgen"]})
        db.set_avatar(conn, account_id, page["avatar"])
        profile_stats: dict = {}
        if page["tags"]:
            profile_stats["vgen_tags"] = page["tags"][:8]
        if page["reviews"]:
            profile_stats["vgen_reviews"] = page["reviews"]
        if page["rating"]:
            profile_stats["vgen_rating"] = page["rating"]
        if profile_stats:
            db.set_platform_stats(conn, account_id, profile_stats)
        # Platform-authoritative commission state — the marketplace's own
        # OPEN/CLOSED switch, not a bio phrase.
        if page["services_status"] in ("OPEN", "CLOSED"):
            db.set_commission(
                conn, account_id,
                (page["services_status"].lower(), 0.95,
                 "vgen:services_status"), None)
        stats["hydrated"] += 1

        if email := find_email(page["bio"]):
            db.set_contact_email(conn, account_id, email)
        for signal, matched in find_attestations(page["bio"]):
            db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
            stats["attestations"] += 1
        for signal, matched in find_nsfw_flags(page["bio"]):
            db.upsert_content_flag(conn, account_id, "nsfw", signal, matched,
                                   snapshot_id)
            stats["nsfw_flags"] += 1
        if page["mature"]:
            db.upsert_content_flag(conn, account_id, "nsfw", "platform_flag",
                                   "vgen:mature_service", snapshot_id)
            stats["nsfw_flags"] += 1

        emitted: set[int] = set()

        def emit(link, evidence_type: str) -> None:
            platform_id = platforms.get(link.platform)
            if platform_id is None:
                return
            if link.platform == "vgen" and link.handle \
                    and link.handle.lower() == username.lower():
                return
            target_id = db.get_or_create_account(
                conn, platform_id, native_id=link.native_id,
                handle=link.handle or link.native_id, profile_url=link.url,
                discovered_via="bio_link",
                discovery_details={"source_account_id": account_id},
            )
            if target_id == account_id or target_id in emitted:
                return
            emitted.add(target_id)
            claim, hint = "same_person", None
            if link.platform == "website":
                claim, hint = "related", "website"
            elif link.platform == "vgen" and evidence_type == "bio_link":
                claim, hint = "related", "same_platform_mention"
            db.upsert_edge(conn, account_id, target_id,
                           evidence_type=evidence_type,
                           evidence_snapshot_id=snapshot_id,
                           evidence_url=link.url, matched_text=None,
                           claim=claim, relation_hint=hint)
            stats[f"edges_{evidence_type}"] += 1

        # Registered socials (user-entered — normal clustering guards apply).
        for url in page["socials"]:
            for link in find_platform_links(url):
                emit(link, "profile_field")
            for link in find_website_links(url):
                emit(link, "profile_field")
        for link in find_platform_links(page["bio"]) + find_website_links(page["bio"]):
            emit(link, "bio_link")

        if n % 50 == 0:
            conn.commit()
            print(f"  …{n} hydrated")
    db.log_api_usage(conn, "vgen", "profile-page",
                     stats["hydrated"] + stats["deleted"] + stats["fetch_failed"], 0)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harvest", action="store_true",
                        help="walk every category listing and mint the top "
                             "artists by review count")
    parser.add_argument("--top", type=int, default=1000,
                        help="artists to mint from a harvest, best reviews first")
    parser.add_argument("--max-listings", type=int, default=0,
                        help="walk only the first N category listings "
                             "(sampling/testing; 0 = all)")
    parser.add_argument("--exclude-category", action="append", default=[],
                        help="skip listings whose category path starts with "
                             "this root (repeatable; tiers in "
                             "docs/vgen-categories.md)")
    parser.add_argument("--all-categories", action="store_true",
                        help="walk every sampled listing instead of the "
                             "tier-1/2 artist allowlist (ARTIST_ROOTS)")
    parser.add_argument("--max-new", type=int, default=0,
                        help="cap artists minted that have no vgen account "
                             "yet, best reviews first (0 = no cap); known "
                             "accounts always refresh")
    parser.add_argument("--hydrate-known", action="store_true",
                        help="fetch profiles for vgen accounts never hydrated")
    parser.add_argument("--limit", type=int, default=1200,
                        help="max profiles per hydrate-known run")
    args = parser.parse_args()
    if not (args.harvest or args.hydrate_known):
        parser.error("nothing to do: pass --harvest and/or --hydrate-known")

    stats: Counter = Counter()
    with httpx.Client(headers=UA, follow_redirects=True, timeout=30) as client, \
            db.connect() as conn:
        platforms = db.platform_ids(conn)
        if args.harvest:
            harvest(conn, client, platforms, stats, args.top,
                    max_listings=args.max_listings,
                    exclude=args.exclude_category,
                    all_categories=args.all_categories,
                    max_new=args.max_new)
        if args.hydrate_known:
            hydrate_known(conn, client, platforms, args.limit, stats)
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
