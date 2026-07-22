"""DeviantArt discovery via the official public RSS backend (free) +
hydration from the artist's own About page.

Stage 1 (--top N): page backend.deviantart.com's Popular deviations feed
(DeviantArt's documented public RSS service, 60 items/page) until N distinct
authors are collected, minting deviantart accounts with
discovered_via='deviantart_popular' (roster-grade: charting on the popular
feed, like a pixiv ranking). media:rating=adult items carry DeviantArt's own
maturity flag => nsfw platform_flag.

Stage 2 (--hydrate-known): fetch deviantart.com/{username}/about pages
(robots-permitted, crawl-delay 1s honored; profile HTML is served openly —
no bot wall, unlike ArtStation) for deviantart accounts never hydrated —
both feed arrivals and existing bio-link targets (the cross-hydration rule).
The page's embedded state carries the artist's registered social links
(user-entered => profile_field edges, no oauth exemption), userId (stable
native_id — usernames can change), watchers, tagline/about text and avatar.

Usage:
  uv run python -m inkpages.discover_deviantart --top 500
  uv run python -m inkpages.discover_deviantart --hydrate-known --limit 600
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

RSS = "https://backend.deviantart.com/rss.xml"

_ITEM = re.compile(r"<item>(.*?)</item>", re.S)
_CREDIT = re.compile(r'<media:credit role="author"[^>]*>([^<]+)</media:credit>')
_RATING = re.compile(r"<media:rating>([^<]+)</media:rating>")
_NEXT = re.compile(r'<atom:link rel="next" href="([^"]+)"')

# The About page embeds its data as window.__INITIAL_STATE__ =
# JSON.parse("<JS string literal containing JSON>").
_STATE = re.compile(r'window\.__INITIAL_STATE__\s*=\s*JSON\.parse\("(.*?)"\);', re.S)


def _js_string(raw: str) -> str:
    """Undo the JS string-literal escaping around the embedded JSON: \\" and
    \\\\ collapse (plus JS-only \\'), every other escape (\\uXXXX, \\n) belongs
    to the inner JSON and stays for json.loads to handle."""
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt in '"\\':
                out.append(nxt)
            elif nxt == "'":
                out.append("'")
            else:
                out.append(c)
                out.append(nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def parse_about_state(page_html: str) -> dict | None:
    m = _STATE.search(page_html)
    if not m:
        return None
    try:
        return json.loads(_js_string(m.group(1)))
    except ValueError:
        return None


def _find_key(obj, key: str, depth: int = 0):
    """First value for key anywhere in the state tree."""
    if depth > 16:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key, depth + 1)
            if found is not None:
                return found
    return None


def profile_fields(state: dict, username: str) -> dict:
    """Pull the artist's own profile data out of the About page state."""
    owner = state.get("profileOwner") or {}
    user = owner.get("user") or {}
    about = _find_key(state, "about") or {}
    text = about.get("textContent") or {}
    excerpt = (text.get("excerpt") or "").strip()
    # The full about body lives in a rich-text JSON blob; URLs inside it are
    # plain text within that JSON, so the ASCII link patterns match directly.
    markup = ""
    html_block = text.get("html") or {}
    if isinstance(html_block, dict):
        markup = html_block.get("markup") or ""
    social = _find_key(state, "socialLinks") or []
    return {
        "user_id": user.get("userId") or _find_key(state, "gruserId"),
        "avatar": user.get("usericon"),
        "watchers": (owner.get("stats") or {}).get("watchers"),
        "tagline": (owner.get("tagline") or "").strip(),
        "excerpt": excerpt,
        "markup": markup,
        "website": (about.get("website") or "").strip(),
        "social": [s.get("value") for s in social
                   if isinstance(s, dict) and s.get("value")],
    }


# The overall Popular feed paginates ~6 pages deep (~290 distinct authors);
# art-category-scoped popular feeds extend the pool to whatever --top asks.
FEED_QUERIES = ("boost%3Apopular",
                "boost%3Apopular+in%3Adigitalart",
                "boost%3Apopular+in%3Atraditional",
                "boost%3Apopular+in%3Afanart")


def harvest(conn, client, platforms, stats, top: int) -> None:
    """Walk the Popular feeds until `top` distinct authors are seen."""
    authors: dict[str, dict] = {}
    pages = 0
    feeds = iter(FEED_QUERIES)
    url = f"{RSS}?type=deviation&q={next(feeds)}"
    while len(authors) < top and pages < 120:
        if url is None:
            q = next(feeds, None)
            if q is None:
                break
            url = f"{RSS}?type=deviation&q={q}"
        time.sleep(1.0)
        resp = fetch_page(client, url)
        if resp is None or resp.status_code != 200:
            stats["feed_failed"] += 1
            url = None
            continue
        pages += 1
        items = _ITEM.findall(resp.text)
        if not items:
            url = None
            continue
        for item in items:
            credits = _CREDIT.findall(item)
            names = [c for c in credits if not c.startswith("http")]
            avatars = [c for c in credits if c.startswith("http")]
            if not names:
                continue
            username = htmllib.unescape(names[0]).strip()
            rating = _RATING.search(item)
            row = authors.setdefault(username, {
                "username": username,
                "avatar": avatars[0] if avatars else None,
                "adult": False,
                "rank": len(authors) + 1,   # first-appearance order
                "items": 0,
            })
            row["items"] += 1
            row["adult"] = row["adult"] or (
                rating is not None and rating.group(1).strip() == "adult")
        nxt = _NEXT.search(resp.text)
        url = htmllib.unescape(nxt.group(1)) if nxt else None
    db.log_api_usage(conn, "deviantart", "rss-popular", pages, 0)
    print(f"deviantart rss: {len(authors)} distinct authors across {pages} pages")

    for row in sorted(authors.values(), key=lambda r: r["rank"])[:top]:
        account_id = db.get_or_create_account(
            conn, platforms["deviantart"],
            native_id=None,
            handle=row["username"],
            display_name=row["username"],
            profile_url=f"https://www.deviantart.com/{row['username'].lower()}",
            discovered_via="deviantart_popular",
            discovery_details={"source": "rss_popular", "rank": row["rank"],
                               "items_on_feed": row["items"]},
        )
        stats["accounts"] += 1
        if db.is_suppressed(conn, account_id):
            stats["skipped_suppressed"] += 1
            continue
        db.set_avatar(conn, account_id, row["avatar"])
        snapshot_id = db.insert_snapshot(
            conn, account_id, bio_text=None, display_name=row["username"],
            followers_count=None, following_count=None,
            raw={"source": "rss_popular", "rank": row["rank"],
                 "items_on_feed": row["items"], "adult_item": row["adult"]},
            fetch_source="deviantart:rss",
        )
        if row["adult"]:
            # DeviantArt's own per-deviation maturity flag, set by the artist.
            db.upsert_content_flag(conn, account_id, "nsfw", "platform_flag",
                                   "deviantart:mature_on_popular", snapshot_id)
            stats["nsfw_flags"] += 1
    conn.commit()


def hydrate_known(conn, client, platforms, limit, stats) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """select a.id, a.handle::text from accounts a
               join platforms p on p.id = a.platform_id
               where p.slug = 'deviantart' and a.last_hydrated is null
                 and a.status not in ('deleted', 'hidden')
               order by a.id limit %s""",
            (limit,),
        )
        todo = cur.fetchall()
    print(f"hydrating {len(todo)} deviantart about pages")
    for n, (account_id, username) in enumerate(todo, 1):
        if db.is_suppressed(conn, account_id):
            stats["skipped_suppressed"] += 1
            continue
        time.sleep(1.0)  # robots.txt crawl-delay
        resp = fetch_page(client,
                          f"https://www.deviantart.com/{username.lower()}/about")
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
            # Throttled/blocked: leave last_hydrated null so a later run
            # retries rather than recording a false empty profile.
            stats[f"http_{resp.status_code}"] += 1
            continue
        state = parse_about_state(resp.text)
        if state is None:
            stats["state_parse_failed"] += 1
            continue
        page = profile_fields(state, username)
        bio = "\n".join(filter(None, [page["tagline"], page["excerpt"]]))
        snapshot_id = db.insert_snapshot(
            conn, account_id, bio_text=bio or None, display_name=username,
            followers_count=page["watchers"], following_count=None,
            raw={"user_id": page["user_id"], "watchers": page["watchers"],
                 "social": page["social"], "website": page["website"]},
            fetch_source="deviantart:about",
        )
        with conn.cursor() as cur:
            cur.execute(
                """update accounts
                   set status = case when status = 'hidden' then 'hidden'
                                     else 'active' end,
                       followers_count = coalesce(%s, followers_count),
                       last_hydrated = now()
                   where id = %s""",
                (page["watchers"], account_id))
            if page["user_id"]:
                # Stable id (usernames mutate); guarded against a duplicate
                # row that already claimed it.
                cur.execute(
                    """update accounts set native_id = %(nid)s
                       where id = %(id)s and native_id is null
                         and not exists (select 1 from accounts
                                         where platform_id = %(pf)s
                                           and native_id = %(nid)s)""",
                    {"nid": str(page["user_id"]), "id": account_id,
                     "pf": platforms["deviantart"]})
        db.set_avatar(conn, account_id, page["avatar"])
        stats["hydrated"] += 1

        scan_text = "\n".join(filter(None, [bio, page["markup"]]))
        if email := find_email(scan_text):
            db.set_contact_email(conn, account_id, email)
        if comm := find_commission_status(scan_text):
            db.set_commission(conn, account_id, comm, None)
        for signal, matched in find_attestations(scan_text):
            db.upsert_attestation(conn, account_id, signal, matched, snapshot_id)
            stats["attestations"] += 1
        for signal, matched in find_nsfw_flags(scan_text):
            db.upsert_content_flag(conn, account_id, "nsfw", signal, matched,
                                   snapshot_id)
            stats["nsfw_flags"] += 1

        emitted: set[int] = set()

        def emit(link, evidence_type: str) -> None:
            platform_id = platforms.get(link.platform)
            if platform_id is None:
                return
            if link.platform == "deviantart" and link.handle \
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
            claim = "related" if link.platform == "website" else "same_person"
            db.upsert_edge(conn, account_id, target_id,
                           evidence_type=evidence_type,
                           evidence_snapshot_id=snapshot_id,
                           evidence_url=link.url, matched_text=None,
                           claim=claim,
                           relation_hint="website" if claim == "related" else None)
            stats[f"edges_{evidence_type}"] += 1

        # Registered profile fields (user-entered on DeviantArt — normal
        # clustering guards apply, no oauth exemption).
        for url in page["social"] + ([page["website"]] if page["website"] else []):
            for link in find_platform_links(url):
                emit(link, "profile_field")
            for link in find_website_links(url):
                emit(link, "profile_field")
        # Links written into the about text itself (markup JSON keeps hrefs
        # as plain text, so the ASCII patterns match in place).
        for link in find_platform_links(scan_text) + find_website_links(scan_text):
            emit(link, "bio_link")

        if n % 50 == 0:
            conn.commit()
            print(f"  …{n} hydrated")
    db.log_api_usage(conn, "deviantart", "about-page",
                     stats["hydrated"] + stats["deleted"] + stats["fetch_failed"], 0)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=0,
                        help="harvest the popular feed until this many "
                             "distinct authors are collected")
    parser.add_argument("--hydrate-known", action="store_true",
                        help="fetch About pages for accounts never hydrated")
    parser.add_argument("--limit", type=int, default=600,
                        help="max About pages per hydrate-known run")
    args = parser.parse_args()
    if not (args.top or args.hydrate_known):
        parser.error("nothing to do: pass --top N and/or --hydrate-known")

    stats: Counter = Counter()
    with httpx.Client(headers=UA, follow_redirects=True, timeout=30) as client, \
            db.connect() as conn:
        platforms = db.platform_ids(conn)
        if args.top:
            harvest(conn, client, platforms, stats, args.top)
        if args.hydrate_known:
            hydrate_known(conn, client, platforms, args.limit, stats)
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
