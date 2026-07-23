"""Cross-hydrate known tumblr accounts via the official API (free, keyed).

GET /v2/blog/{blog}/info?api_key= returns the public blog record: uuid (the
stable native id — blog names are renameable), title (display name),
description (the sidebar bio, as HTML), avatar[] (largest first), is_nsfw,
total_posts and `updated` (unix ts of the latest post). No follower count is
public for other people's blogs — only the blog owner's own token sees it.

The description is HTML, so anchors are linkified into the snapshot's
bio_text (`<a href="URL">text</a>` -> "URL text") before the tags are
stripped. That keeps every edge derivable from bio_text alone, which is what
lets reextract re-parse tumblr snapshots like any other bio.

Enrichment-only (cross-hydration rule): tumblr blogs are surfaced by other
sources' bios and hub pages, this fills them in. Discovery via the tagged
feed can come later.

Rate limits: newly registered keys get 1,000 requests/hour and 5,000/day —
the default pace stays just under the hourly ceiling.

Usage: uv run python -m inkpages.discover_tumblr --hydrate-known [--limit N]
"""
import argparse
import re
import time
from collections import Counter
from datetime import datetime, timezone
from html import unescape

import httpx
from psycopg.rows import dict_row

from . import db
from .extract import (find_attestations, find_commission_status, find_email,
                      find_nsfw_flags, find_platform_links, find_website_links)

API = "https://api.tumblr.com/v2/blog/{blog}/info"
# 1,000 requests/hour is the new-app ceiling; 3.7s/request ≈ 970/hour.
PACE_S = 3.7
UA = {"User-Agent": "inkpages/0.1 (no-AI artist directory)"}

_ANCHOR = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>""", re.I)
_TAG_STRIP = re.compile(r"<(?:script|style)[^>]*>.*?</(?:script|style)>|<[^>]+>",
                        re.S | re.I)


def bio_from_description(html: str | None) -> str:
    """HTML sidebar description -> plain bio text with link hrefs inlined."""
    if not html:
        return ""
    text = _ANCHOR.sub(lambda m: f" {m.group(1)} ", html)
    text = _TAG_STRIP.sub(" ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def best_avatar(blog: dict) -> str | None:
    avatars = [a for a in (blog.get("avatar") or []) if a.get("url")]
    if not avatars:
        return None
    return max(avatars, key=lambda a: a.get("width") or 0)["url"]


def fetch_blog(client: httpx.Client, api_key: str, name: str):
    """-> blog dict | 'gone' | 'walled'. Raises on transport/5xx errors."""
    resp = client.get(API.format(blog=name), params={"api_key": api_key},
                      timeout=20)
    if resp.status_code == 429:
        print("  rate limited — sleeping 300s")
        time.sleep(300)
        resp = client.get(API.format(blog=name), params={"api_key": api_key},
                          timeout=20)
        if resp.status_code == 429:
            raise RuntimeError("rate limited twice; stop and resume later")
    if resp.status_code == 404:
        return "gone"
    if resp.status_code in (401, 403):
        # Password-protected / age-gated / suspended blogs answer 403. Not
        # deleted — just not readable by an api_key-only client.
        return "walled"
    resp.raise_for_status()
    return resp.json()["response"]["blog"]


def process_account(conn, platforms, account, blog: dict, stats: Counter) -> None:
    account_id = account["id"]
    name = blog.get("name") or account["handle"]
    title = (blog.get("title") or "").strip() or None
    bio = bio_from_description(blog.get("description"))
    updated = blog.get("updated")
    last_post = (datetime.fromtimestamp(updated, tz=timezone.utc)
                 if updated else None)

    with conn.cursor() as cur:
        # Update this row directly: get_or_create's claim-by-handle path is
        # for new rows, and the uuid is the identity we want pinned here.
        cur.execute(
            """update accounts
               set native_id = coalesce(native_id, %s),
                   handle = %s,
                   display_name = coalesce(%s, display_name),
                   profile_url = coalesce(%s, profile_url),
                   status = case when status = 'hidden' then 'hidden'
                                 else 'active' end,
                   last_hydrated = now()
               where id = %s""",
            (blog.get("uuid"), name, title, blog.get("url"), account_id))

    if db.is_suppressed(conn, account_id):
        stats["skipped_suppressed"] += 1
        return

    snapshot_id = db.insert_snapshot(
        conn, account_id, bio_text=bio or None, display_name=title,
        followers_count=None, following_count=None,
        raw={"uuid": blog.get("uuid"), "name": name, "url": blog.get("url"),
             "total_posts": blog.get("total_posts"),
             "is_nsfw": blog.get("is_nsfw"), "ask": blog.get("ask"),
             "updated": updated, "description_html": blog.get("description")},
        fetch_source="tumblr:blog/info",
    )
    stats["snapshots"] += 1
    if avatar := best_avatar(blog):
        db.set_avatar(conn, account_id, avatar)
        stats["avatars"] += 1
    if last_post:
        db.touch_last_post(conn, account_id, last_post)
    db.set_platform_stats(conn, account_id, {
        "tumblr_posts": blog.get("total_posts"),
        "tumblr_is_nsfw": bool(blog.get("is_nsfw")),
    })

    if blog.get("is_nsfw"):
        db.upsert_content_flag(conn, account_id, "nsfw", "platform_flag",
                               "tumblr is_nsfw", snapshot_id)
        stats["nsfw_flags"] += 1

    scan = "\n".join(filter(None, [bio, title]))
    if email := find_email(bio):
        db.set_contact_email(conn, account_id, email)
    if comm := find_commission_status(scan):
        db.set_commission(conn, account_id, comm, None)
        stats["commission_signals"] += 1
    for signal, matched in find_attestations(bio):
        db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
        stats["attestations"] += 1
    for signal, matched in find_nsfw_flags(bio):
        db.upsert_content_flag(conn, account_id, "nsfw", signal, matched,
                               snapshot_id)
        stats["nsfw_flags"] += 1

    # Edges come from bio_text only — the same text reextract will re-parse,
    # so no edge churns between this worker and a later reextract pass.
    emitted: set[int] = set()
    for link in find_platform_links(bio) + find_website_links(bio):
        platform_id = platforms.get(link.platform)
        if platform_id is None:
            continue
        if link.platform == "tumblr" and link.handle \
                and link.handle.lower() == name.lower():
            continue
        target_id = db.get_or_create_account(
            conn, platform_id, native_id=link.native_id,
            handle=link.handle or link.native_id, profile_url=link.url,
            discovered_via="bio_link",
            discovery_details={"source_account_id": account_id,
                               "via": "tumblr_description"},
        )
        if target_id == account_id or target_id in emitted:
            continue
        emitted.add(target_id)
        is_site = link.platform == "website"
        db.upsert_edge(conn, account_id, target_id, evidence_type="bio_link",
                       evidence_snapshot_id=snapshot_id,
                       evidence_url=link.url, matched_text=None,
                       claim="related" if is_site else "same_person",
                       relation_hint="website" if is_site else None)
        stats["edges"] += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hydrate-known", action="store_true",
                        help="enrich held tumblr accounts never fetched")
    parser.add_argument("--refresh", action="store_true",
                        help="also re-fetch hydrated accounts (oldest first)")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--pace", type=float, default=PACE_S,
                        help=f"seconds between requests (default {PACE_S})")
    args = parser.parse_args()
    if not (args.hydrate_known or args.refresh):
        parser.error("nothing to do: pass --hydrate-known (and/or --refresh)")

    api_key = db.env_var("TUMBLR_API_KEY")
    if not api_key:
        raise SystemExit("TUMBLR_API_KEY missing from .env")

    stats: Counter = Counter()
    with db.connect() as conn, httpx.Client(headers=UA) as client:
        platforms = db.platform_ids(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""select a.id, a.handle::text
                   from accounts a
                   where a.platform_id = %s
                     and a.status not in ('deleted', 'hidden')
                     {'' if args.refresh else 'and a.last_hydrated is null'}
                   order by a.last_hydrated asc nulls first, a.id
                   limit %s""",
                (platforms["tumblr"], args.limit))
            accounts = cur.fetchall()
        print(f"{len(accounts)} tumblr accounts to hydrate "
              f"(~{len(accounts) * args.pace / 60:.0f} min at {args.pace}s)")

        for account in accounts:
            time.sleep(args.pace)
            try:
                blog = fetch_blog(client, api_key, account["handle"])
            except RuntimeError as exc:
                print(f"stopping: {exc}")
                break
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                stats["fetch_failed"] += 1
                print(f"  fetch failed for {account['handle']}: {exc}")
                continue
            if blog in ("gone", "walled"):
                with conn.cursor() as cur:
                    cur.execute(
                        """update accounts
                           set status = case when %s then 'deleted'
                                             else status end,
                               last_hydrated = now()
                           where id = %s""",
                        (blog == "gone", account["id"]))
                stats["deleted" if blog == "gone" else "walled"] += 1
                conn.commit()
                continue
            process_account(conn, platforms, account, blog, stats)
            stats["hydrated"] += 1
            conn.commit()
            if stats["hydrated"] % 50 == 0:
                print(f"  …{stats['hydrated']} hydrated")

        db.log_api_usage(conn, "tumblr", "blog/info",
                         sum(stats[k] for k in ("hydrated", "deleted", "walled",
                                                "fetch_failed")), 0)
        conn.commit()

    print("done:", dict(stats))


if __name__ == "__main__":
    main()
