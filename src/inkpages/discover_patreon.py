"""Patreon discovery via Graphtreon's public rankings (free) + hydration from
the creator's own Patreon page.

Stage 1 (--harvest): crawl Graphtreon's per-category top-50 lists — four
metrics (paid members, earnings, growth, free members) across the art
categories (drawing-painting / comics / animation, SFW + adult) — and mint
patreon accounts with discovered_via='patreon_ranking' (a roster source, like
charting on a pixiv ranking). Graphtreon's robots.txt allows crawling; its
paid API ($780/mo) is for daily-stats access we don't need. Adult-category
listings carry Patreon's own self-declared adult flag => nsfw platform_flag.

Stage 2 (--hydrate-known): fetch patreon.com/{vanity} public profile pages
(allowed for generic agents; /api/ is disallowed and never touched) for
patreon accounts never hydrated — both ranking arrivals and accounts other
artists' bios referenced. The page's schema.org JSON-LD carries the creator's
registered social links (sameAs) => profile_field same_person edges, and the
about text => normal bio extraction. This is the artist's own published
surface, snapshotted as evidence like any other.

Usage:
  uv run python -m inkpages.discover_patreon --harvest
  uv run python -m inkpages.discover_patreon --hydrate-known --limit 300
"""
import argparse
import html as htmllib
import json
import re
import time
from collections import Counter

import httpx

from . import db
from .crawl_links import UA, fetch_page
from .extract import (find_attestations, find_commission_status, find_email,
                      find_nsfw_flags, find_platform_links, find_website_links)

GRAPHTREON = "https://graphtreon.com"
METRICS = ("top-patreon-creators", "top-patreon-earners",
           "top-growing-patreon", "top-creators-by-free-members")
CATEGORIES = ("drawing-painting", "comics", "animation",
              "adult-drawing-painting", "adult-comics", "adult-animation")

# Patreon's own footer/company socials must never become artist links.
SERVICE_HANDLES = {("twitter", "patreon"), ("instagram", "patreon"),
                   ("youtube", "patreon"), ("facebook", "patreon"),
                   ("twitch", "patreon")}

# Rows are keyed on the creator column — the only structure shared by all
# four list templates (only top-patreon-creators renders a rank column, so
# rank is list position, which is what the visible rank shows anyway).
_ROW_SPLIT = re.compile(r'class="creator-column')
_ROW_VANITY = re.compile(r'href="/creator/([A-Za-z0-9_.~-]+)"')
_ROW_CAMPAIGN = re.compile(r"/campaign/(\d+)/")
_ROW_NAME = re.compile(r'creator-name">\s*(.*?)\s*<', re.S)
_ROW_BLURB = re.compile(r'<p class="no-margin">\s*(.*?)\s*</p>', re.S)
_TAGS = re.compile(r"<[^>]+>")
_RESIDUAL_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>\s*(.*?)\s*</script>', re.S)


def parse_ranking(page_html: str):
    """Yield (rank, vanity, campaign_id, name, blurb) per creator row."""
    seen: set[str] = set()
    for block in _ROW_SPLIT.split(page_html)[1:]:
        vanity = _ROW_VANITY.search(block)
        if not vanity or vanity.group(1) in seen:
            continue
        seen.add(vanity.group(1))
        campaign = _ROW_CAMPAIGN.search(block)
        name = _ROW_NAME.search(block)
        blurb = _ROW_BLURB.search(block)
        yield (len(seen),
               vanity.group(1),
               campaign.group(1) if campaign else None,
               htmllib.unescape(name.group(1)) if name else None,
               htmllib.unescape(_TAGS.sub("", blurb.group(1))) if blurb else None)


def harvest(conn, client, platforms, stats, max_new: int = 0) -> None:
    """Crawl every metric x category list; first sighting wins the details,
    an adult-category sighting anywhere wins the adult flag. max_new caps how
    many creators NOT already in the db are added (best chart position first);
    already-known creators always refresh regardless."""
    creators: dict[str, dict] = {}
    for metric in METRICS:
        for category in CATEGORIES:
            time.sleep(1.0)
            resp = fetch_page(client, f"{GRAPHTREON}/{metric}/{category}")
            if resp is None or resp.status_code != 200:
                stats[f"list_failed_{metric}/{category}"] += 1
                continue
            stats["lists_fetched"] += 1
            for rank, vanity, campaign, name, blurb in parse_ranking(resp.text):
                row = creators.setdefault(vanity, {
                    "vanity": vanity, "campaign": campaign, "name": name,
                    "blurb": blurb, "metric": metric, "category": category,
                    "rank": rank, "adult": False,
                })
                row["campaign"] = row["campaign"] or campaign
                row["adult"] = row["adult"] or category.startswith("adult-")
    db.log_api_usage(conn, "graphtreon", "top-lists", stats["lists_fetched"], 0)
    print(f"graphtreon: {len(creators)} distinct creators "
          f"across {stats['lists_fetched']} lists")

    rows = sorted(creators.values(), key=lambda r: (r["rank"] or 999))
    if max_new:
        with conn.cursor() as cur:
            cur.execute(
                """select handle::text from accounts where platform_id = %s""",
                (platforms["patreon"],))
            known = {h for (h,) in cur.fetchall()}
        new_seen = 0
        kept = []
        for row in rows:
            if row["vanity"] in known:
                kept.append(row)
            elif new_seen < max_new:
                kept.append(row)
                new_seen += 1
            else:
                stats["skipped_over_max_new"] += 1
        rows = kept

    for row in rows:
        account_id = db.get_or_create_account(
            conn, platforms["patreon"],
            native_id=row["campaign"],
            handle=row["vanity"],
            display_name=row["name"],
            profile_url=f"https://www.patreon.com/{row['vanity']}",
            discovered_via="patreon_ranking",
            discovery_details={"source": "graphtreon", "metric": row["metric"],
                               "category": row["category"], "rank": row["rank"]},
        )
        stats["accounts"] += 1
        if db.is_suppressed(conn, account_id):
            stats["skipped_suppressed"] += 1
            continue
        # Ranking-row snapshot: provenance for the adult flag and any
        # attestation in the blurb. Blurbs are truncated, so no link
        # extraction here — the hydration pass reads the full page.
        snapshot_id = db.insert_snapshot(
            conn, account_id, bio_text=row["blurb"], display_name=row["name"],
            followers_count=None, following_count=None,
            raw={"source": "graphtreon", "metric": row["metric"],
                 "category": row["category"], "rank": row["rank"]},
            fetch_source="graphtreon:ranking",
        )
        for signal, matched in find_attestations(row["blurb"]):
            db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
            stats["attestations"] += 1
        if row["adult"]:
            db.upsert_content_flag(conn, account_id, "nsfw", "platform_flag",
                                   "patreon:adult_category", snapshot_id)
            stats["nsfw_flags"] += 1
    conn.commit()


def _walk_ld(node, out) -> None:
    """Collect sameAs / description / name / image from JSON-LD, recursively."""
    if isinstance(node, dict):
        if isinstance(node.get("sameAs"), list):
            out["sameAs"].extend(u for u in node["sameAs"] if isinstance(u, str))
        for key in ("description", "name"):
            if isinstance(node.get(key), str) and not out.get(key):
                out[key] = node[key]
        image = node.get("image")
        if not out.get("image"):
            if isinstance(image, str):
                out["image"] = image
            elif isinstance(image, dict) and isinstance(image.get("contentUrl"), str):
                out["image"] = image["contentUrl"]
        for value in node.values():
            _walk_ld(value, out)
    elif isinstance(node, list):
        for item in node:
            _walk_ld(item, out)


def parse_creator_page(page_html: str) -> dict:
    """The page carries several JSON-LD blocks: the creator's ProfilePage
    (root has mainEntity) plus Patreon's own Organization schema. Only the
    creator block feeds extraction — the Organization block's name/socials
    are Patreon the company, not the artist."""
    roots = []
    for blob in _JSON_LD.findall(page_html):
        try:
            roots.append(json.loads(blob))
        except ValueError:
            continue
    profile = [r for r in roots if isinstance(r, dict) and "mainEntity" in r]
    out: dict = {"sameAs": []}
    for root in profile or roots:
        _walk_ld(root, out)
    # Patreon double-escapes entities inside the JSON-LD ("\\u0026"), which
    # one json.loads pass leaves as a literal & in the text.
    for key in ("name", "description", "image"):
        if out.get(key):
            out[key] = _RESIDUAL_ESC.sub(
                lambda m: chr(int(m.group(1), 16)), out[key])
    out["sameAs"] = [
        _RESIDUAL_ESC.sub(lambda m: chr(int(m.group(1), 16)), u)
        for u in dict.fromkeys(out["sameAs"])]
    return out


def hydrate_known(conn, client, platforms, limit, stats) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """select a.id, a.handle::text from accounts a
               join platforms p on p.id = a.platform_id
               where p.slug = 'patreon' and a.last_hydrated is null
                 and a.status not in ('deleted', 'hidden')
               order by a.id limit %s""",
            (limit,),
        )
        todo = cur.fetchall()
    print(f"hydrating {len(todo)} patreon pages")
    for n, (account_id, vanity) in enumerate(todo, 1):
        if db.is_suppressed(conn, account_id):
            stats["skipped_suppressed"] += 1
            continue
        time.sleep(1.2)
        resp = fetch_page(client, f"https://www.patreon.com/{vanity}")
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
            # Blocked/throttled: leave last_hydrated null so the next run
            # retries instead of recording a false empty profile.
            stats[f"http_{resp.status_code}"] += 1
            continue
        page = parse_creator_page(resp.text)
        bio = page.get("description") or ""
        snapshot_id = db.insert_snapshot(
            conn, account_id, bio_text=bio, display_name=page.get("name"),
            followers_count=None, following_count=None,
            raw={"sameAs": page["sameAs"], "name": page.get("name"),
                 "image": page.get("image")},
            fetch_source="patreon:page",
        )
        with conn.cursor() as cur:
            cur.execute(
                """update accounts
                   set status = case when status = 'hidden' then 'hidden'
                                     else 'active' end,
                       display_name = coalesce(%s, display_name),
                       last_hydrated = now()
                   where id = %s""",
                (page.get("name"), account_id))
        db.set_avatar(conn, account_id, page.get("image"))
        stats["hydrated"] += 1

        if email := find_email(bio):
            db.set_contact_email(conn, account_id, email)
        if comm := find_commission_status(bio):
            db.set_commission(conn, account_id, comm, None)
        for signal, matched in find_attestations(bio):
            db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
            stats["attestations"] += 1
        for signal, matched in find_nsfw_flags(bio):
            db.upsert_content_flag(conn, account_id, "nsfw", signal, matched,
                                   snapshot_id)
            stats["nsfw_flags"] += 1

        emitted: set[int] = set()

        def emit(link, evidence_type: str) -> None:
            platform_id = platforms.get(link.platform)
            if platform_id is None:
                return
            if (link.platform, (link.handle or "").lower()) in SERVICE_HANDLES:
                return
            if link.platform == "patreon" and link.handle \
                    and link.handle.lower() == vanity.lower():
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
            claim = "related" if link.platform == "website" else "same_person"
            db.upsert_edge(conn, account_id, target_id,
                           evidence_type=evidence_type,
                           evidence_snapshot_id=snapshot_id,
                           evidence_url=link.url, matched_text=None,
                           claim=claim,
                           relation_hint="website" if claim == "related" else None)
            stats[f"edges_{evidence_type}"] += 1

        # Registered social connections (user-entered on Patreon — normal
        # clustering guards apply, no oauth exemption).
        for url in page["sameAs"]:
            for link in find_platform_links(url):
                emit(link, "profile_field")
            for link in find_website_links(url):
                emit(link, "profile_field")
        # Links written into the about text itself.
        for link in find_platform_links(bio) + find_website_links(bio):
            emit(link, "bio_link")

        if n % 50 == 0:
            conn.commit()
            print(f"  …{n} hydrated")
    db.log_api_usage(conn, "patreon", "profile-page",
                     stats["hydrated"] + stats["deleted"] + stats["fetch_failed"], 0)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harvest", action="store_true",
                        help="crawl Graphtreon's art-category top lists")
    parser.add_argument("--max-new", type=int, default=0,
                        help="cap creators not already in the db added per "
                             "harvest, best chart position first (0 = no cap)")
    parser.add_argument("--hydrate-known", action="store_true",
                        help="fetch patreon pages for accounts never hydrated")
    parser.add_argument("--limit", type=int, default=400,
                        help="max patreon pages per hydrate-known run")
    args = parser.parse_args()
    if not (args.harvest or args.hydrate_known):
        parser.error("nothing to do: pass --harvest and/or --hydrate-known")

    stats: Counter = Counter()
    with httpx.Client(headers=UA, follow_redirects=True) as client, \
            db.connect() as conn:
        platforms = db.platform_ids(conn)
        if args.harvest:
            harvest(conn, client, platforms, stats, max_new=args.max_new)
        if args.hydrate_known:
            hydrate_known(conn, client, platforms, args.limit, stats)
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
