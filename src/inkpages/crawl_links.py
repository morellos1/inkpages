"""Resolve artist-published shortened links (t.co etc.) and crawl link-hub
pages (Linktree, Carrd, potofu.me, lit.link) to extract the links inside.

Both are the artist's own published surfaces, so results become ordinary
identity_edges with the fetched page snapshot as evidence. Twitter itself is
never fetched (t.co is a redirect hop, not Twitter content); display-only
platforms are never fetched.

Usage: uv run python -m inkpages.crawl_links [--max-hubs 100]
"""
import argparse
import re
import time
from collections import Counter
from html import unescape as _unescape

import httpx
from psycopg.rows import dict_row

from . import db
from .extract import (SHORTENER_DOMAINS, find_attestations,
                      find_commission_status, find_email, find_nsfw_flags,
                      find_platform_links, find_short_links, find_website_links)

# SQL-side prefilter for snapshots that mention any shortener — kept in sync
# with find_short_links by deriving from the same domain list. Loose superset
# is fine: find_short_links does the boundary-exact extraction.
_SHORTENER_SCAN_RE = "(%s)/" % "|".join(re.escape(d) for d in SHORTENER_DOMAINS)

_HOST_OF = re.compile(r"^https?://([^/]+)", re.I)


def _same_host(a: str, b: str) -> bool:
    ha, hb = _HOST_OF.match(a), _HOST_OF.match(b)
    return bool(ha and hb) and ha.group(1).lower() == hb.group(1).lower()

# Hub services' own footer/social accounts. Every hub page carries them, so
# they'd otherwise become massively-shared targets — or worse, an artist:
# TSUNAGU's twitter bio links its demo profile, whose page links back, and
# that reciprocal pair auto-merged into a fake "tsunagu-cloud" artist.
SERVICE_ACCOUNTS = {
    ("twitter", "tsunagu_cloud"), ("tsunagu", "test_account"),
    ("twitter", "linktr_ee"), ("twitter", "carrd"), ("twitter", "potofu_me"),
    ("twitter", "twpf"), ("twitter", "profcard_info"), ("twitter", "lit_link"),
}

_TAG_STRIP = re.compile(r"<(?:script|style)[^>]*>.*?</(?:script|style)>|<[^>]+>",
                        re.IGNORECASE | re.DOTALL)
# Attribute/JSON-scoped URL extraction for hub pages: personal-website links
# only count when they appear as an href or a "url" value, so raw page markup
# (script sources, CDN assets, og tags in text) can't mint junk accounts.
_LINKED_URL = re.compile(r"""(?:href\s*=|"url"\s*:)\s*["'](https?://[^"']+)["']""",
                         re.IGNORECASE)

HUB_PLATFORMS = ("linktree", "carrd", "potofu", "litlink", "biosite",
                 "profcard", "twpf", "tsunagu")
# Browser-like headers: Linktree 403s obvious bot UAs while serving the same
# public page to browsers. We fetch one page per hub at a polite rate.
UA = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
}


def fetch_page(client: httpx.Client, url: str):
    """GET a public page; on 403 fall back to curl — some hosts (Linktree)
    fingerprint the TLS handshake and block python clients while serving the
    identical public page to browsers/curl."""
    import subprocess
    from types import SimpleNamespace

    try:
        resp = client.get(url, follow_redirects=True, timeout=20)
        if resp.status_code != 403:
            return resp
    except (httpx.HTTPError, httpx.InvalidURL):
        return None
    try:
        proc = subprocess.run(
            ["curl", "-sS", "-L", "--max-time", "25", "-A", UA["User-Agent"],
             "-w", "\n%{http_code}", url],
            capture_output=True, text=True, timeout=35)
        body, _, code = proc.stdout.rpartition("\n")
        return SimpleNamespace(status_code=int(code or 0), text=body)
    except (subprocess.SubprocessError, ValueError):
        return None


def resolve_url(conn, client: httpx.Client, url: str, cache: dict) -> str | None:
    """Resolve a shortener with a cross-run DB cache (resolved_links): a short
    URL's destination is effectively immutable, so each is fetched exactly
    once, throttled. Failures aren't cached — retried next run.

    The client must NOT send a browser User-Agent: t.co serves browsers a 200
    HTML interstitial instead of the 301, which once poisoned the cache with
    3.5k self-resolutions."""
    if url in cache:
        return cache[url]
    time.sleep(0.4)
    final = None
    try:
        resp = client.head(url, follow_redirects=True, timeout=15)
        final = str(resp.url)
    except httpx.HTTPError:
        try:
            with client.stream("GET", url, follow_redirects=True, timeout=15) as resp:
                final = str(resp.url)
        except httpx.HTTPError:
            pass
    if final is not None and _same_host(final, url):
        # Never left the shortener (interstitial, error page, dead link) —
        # that is a failed resolution, not a destination. Don't cache in DB.
        final = None
    cache[url] = final
    if final:
        with conn.cursor() as cur:
            cur.execute(
                """insert into resolved_links (short_url, final_url)
                   values (%s, %s) on conflict (short_url) do nothing""",
                (url, final))
    return final


def resolve_shorteners(conn, client, platforms, stats):
    """Latest snapshot per account containing shortener links -> resolve ->
    platform links become edges with the shortener chain as evidence_url."""
    with conn.cursor(row_factory=dict_row) as cur:
        # Bio plus the Twitter location field — artists park links there too.
        cur.execute(
            """select distinct on (s.account_id)
                      s.account_id, s.id as snapshot_id, p.slug as platform,
                      coalesce(s.bio_text, '') || ' ' || coalesce(s.raw ->> 'location', '') as scan_text
               from account_snapshots s
               join accounts a on a.id = s.account_id
               join platforms p on p.id = a.platform_id
               where coalesce(s.bio_text, '') || ' ' || coalesce(s.raw ->> 'location', '')
                     ~* %s
               order by s.account_id, s.captured_at desc""",
            (_SHORTENER_SCAN_RE,),
        )
        rows = cur.fetchall()
    with conn.cursor() as cur:
        cur.execute("select short_url, final_url from resolved_links")
        cache: dict = dict(cur.fetchall())
    for row in rows:
        if db.is_suppressed(conn, row["account_id"]):
            continue
        for short in find_short_links(row["scan_text"]):
            # A twitter bio's own t.co wrappers are already expanded for free
            # by the API's url entities at hydration — resolving them here is
            # pure duplicate fetch work (~3.5k of them at one point).
            if row["platform"] == "twitter" and short.startswith("https://t.co/"):
                continue
            final = resolve_url(conn, client, short, cache)
            stats["shorteners_resolved"] += 1
            if not final:
                stats["shorteners_failed"] += 1
                continue
            for link in find_platform_links(final) + find_website_links(final):
                platform_id = platforms.get(link.platform)
                if platform_id is None:
                    continue
                target_id = db.get_or_create_account(
                    conn, platform_id,
                    native_id=link.native_id,
                    handle=link.handle or link.native_id,
                    profile_url=link.url,
                    discovered_via="bio_link",
                    discovery_details={"source_account_id": row["account_id"],
                                       "via_shortener": short},
                )
                db.upsert_edge(conn, row["account_id"], target_id,
                               evidence_type="bio_link",
                               evidence_snapshot_id=row["snapshot_id"],
                               evidence_url=f"{short} -> {final}",
                               matched_text=None,
                               claim="related" if link.platform == "website" else "same_person",
                               relation_hint="website" if link.platform == "website" else None)
                stats["edges_from_shorteners"] += 1


# Hubs whose OG tags carry real profile data (og:title = the artist's own
# display name, og:image = profile icon, og:description = their bio text).
# Linktree/Carrd og tags are share banners / boilerplate — never captured.
PROFILE_OG_HUBS = ("potofu",)

_OG_TAG = re.compile(
    r'<meta[^>]+property=["\']og:(title|image|description)["\'][^>]+content=["\']([^"\']*)["\']',
    re.I)
_OG_TAG_REV = re.compile(  # attribute order flipped (content before property)
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:(title|image|description)["\']',
    re.I)


def extract_og(html: str) -> dict:
    og: dict[str, str] = {}
    for key, value in _OG_TAG.findall(html):
        og.setdefault(key.lower(), value.strip())
    for value, key in _OG_TAG_REV.findall(html):
        og.setdefault(key.lower(), value.strip())
    return og


def crawl_hubs(conn, client, platforms, max_hubs, stats):
    """Fetch hub pages that appear in the graph and extract the links inside
    as link_hub edges (hub -> target)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select a.id, a.handle::text, a.profile_url, p.slug as platform
               from accounts a join platforms p on p.id = a.platform_id
               where p.slug = any(%s) and a.profile_url is not null
                 and a.last_hydrated is null
                 and (exists (select 1 from identity_edges e
                              where e.target_account_id = a.id or e.source_account_id = a.id)
                      or exists (select 1 from artist_accounts aa
                                 where aa.account_id = a.id and aa.removed_at is null))
               order by a.id limit %s""",
            (list(HUB_PLATFORMS), max_hubs),
        )
        hubs = cur.fetchall()

    for hub in hubs:
        if db.is_suppressed(conn, hub["id"]):
            continue
        time.sleep(0.8)
        resp = fetch_page(client, hub["profile_url"])
        if resp is not None and resp.status_code == 429:
            time.sleep(8)
            resp = fetch_page(client, hub["profile_url"])
        if resp is None:
            stats["hubs_failed"] += 1
            continue
        if resp.status_code == 404:
            with conn.cursor() as cur:
                cur.execute("update accounts set status = 'deleted', last_hydrated = now() where id = %s",
                            (hub["id"],))
            stats["hubs_404"] += 1
            continue
        if resp.status_code != 200:
            # Blocked or throttled: no snapshot, last_hydrated stays null so
            # the next run retries instead of recording a false empty result.
            stats[f"hubs_http_{resp.status_code}"] += 1
            continue
        with conn.cursor() as cur:
            cur.execute(
                """update accounts
                   set status = case when status = 'hidden' then 'hidden' else 'active' end,
                       last_hydrated = now()
                   where id = %s""",
                (hub["id"],))
        # Hub builders embed links in JSON blobs with escaped slashes.
        html = (resp.text[:800_000]
                .replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
                .replace("&amp;", "&"))
        links = [l for l in find_platform_links(html)
                 if not (l.platform == hub["platform"])
                 and (l.platform, (l.handle or "").lower()) not in SERVICE_ACCOUNTS]
        # Personal websites listed on the hub (attribute-scoped; every other
        # worker extracts them, hubs previously dropped them). More than 5
        # distinct sites reads as a credits/resources dump — skip those.
        site_links = find_website_links(
            "\n".join(dict.fromkeys(m.group(1) for m in _LINKED_URL.finditer(html))))
        if len(site_links) <= 5:
            links += site_links
        # Profile-flavored hubs (potofu): OG tags are the artist's own name,
        # icon and bio — capture them so hub accounts stop being bare slugs.
        og_name = og_desc = None
        if hub["platform"] in PROFILE_OG_HUBS:
            og = extract_og(html)
            og_name = _unescape(og.get("title", "")).strip()
            # Some accounts' og:title carries the service suffix ("name |
            # POTOFU | POTOFU"); the artist's own name is the leading part.
            og_name = re.sub(r"(?:\s*\|\s*POTOFU)+\s*$", "", og_name).strip() or None
            og_desc = _unescape(og.get("description", "")).strip() or None
            if og_name:
                with conn.cursor() as cur:
                    cur.execute("update accounts set display_name = %s where id = %s",
                                (og_name, hub["id"]))
            image = og.get("image", "")
            if image.startswith("http") and "default_profile" not in image:
                db.set_avatar(conn, hub["id"], image)

        link_list = "\n".join(sorted({l.url for l in links}))
        snapshot_id = db.insert_snapshot(
            conn, hub["id"],
            # reextract skips hub_crawl snapshots, so bio_text is display +
            # provenance only: the artist's own words first, then the links.
            bio_text="\n\n".join(filter(None, [og_desc, link_list])) or None,
            display_name=og_name, followers_count=None, following_count=None,
            raw={"url": hub["profile_url"], "status": resp.status_code,
                 "link_count": len(links)},
            fetch_source="hub_crawl",
        )
        stats["hubs_crawled"] += 1
        # The description is self-authored bio text — mine it for the same
        # per-account self-signals a platform bio yields.
        if og_desc:
            for signal, matched in find_attestations(og_desc):
                db.upsert_attestation(conn, hub["id"], signal, matched, snapshot_id)
                stats["hub_attestations"] += 1
            for signal, matched in find_nsfw_flags(og_desc):
                db.upsert_content_flag(conn, hub["id"], "nsfw", signal, matched,
                                       snapshot_id)
                stats["hub_nsfw_flags"] += 1
        # Commission status / contact email announced on the hub page itself
        # (link titles like "Commissions — OPEN"). Lower confidence than a bio.
        page_text = _TAG_STRIP.sub(" ", html)
        if comm := find_commission_status(page_text, multiplier=0.65):
            db.set_commission(conn, hub["id"], comm, None)
            stats["hub_commission_signals"] += 1
        if email := find_email(page_text):
            db.set_contact_email(conn, hub["id"], email)
        produced: set[int] = set()
        # Project-dump heuristic: an artist's own hub links 1–2 accounts per
        # platform (main + alt). Three or more on the SAME platform means the
        # page lists collaborations/credits — those become 'related'
        # connections, never same-person identity claims.
        per_platform = Counter(l.platform for l in links)
        for link in links:
            platform_id = platforms.get(link.platform)
            if platform_id is None:
                continue
            is_credit_dump = per_platform[link.platform] >= 3
            if link.platform == "website":
                claim, hint = "related", "website"
            elif is_credit_dump:
                claim, hint = "related", "hub_credits"
            else:
                claim, hint = "same_person", None
            target_id = db.get_or_create_account(
                conn, platform_id,
                native_id=link.native_id,
                handle=link.handle or link.native_id,
                profile_url=link.url,
                discovered_via="link_hub",
                discovery_details={"hub_account_id": hub["id"]},
            )
            db.upsert_edge(conn, hub["id"], target_id,
                           evidence_type="link_hub",
                           evidence_snapshot_id=snapshot_id,
                           evidence_url=link.url, matched_text=None,
                           claim=claim, relation_hint=hint)
            produced.add(target_id)
            stats["edges_related_hub_credits" if is_credit_dump else "edges_from_hubs"] += 1
        # Retract hub edges the fresh crawl no longer reproduces (page edited,
        # or an earlier parser bug produced a bogus target).
        with conn.cursor() as cur:
            cur.execute(
                """update identity_edges set status = 'retracted'
                   where source_account_id = %s and status = 'present'
                     and evidence_type = 'link_hub'
                     and target_account_id <> all(%s)
                   returning id""",
                (hub["id"], list(produced) or [0]),
            )
            stats["hub_edges_retracted"] += len(cur.fetchall())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-hubs", type=int, default=100)
    parser.add_argument("--recrawl-all", action="store_true",
                        help="re-crawl every hub, not just new/failed ones")
    parser.add_argument("--recrawl-platform",
                        help="re-crawl every hub on ONE platform slug (e.g. "
                             "potofu after an extraction upgrade)")
    args = parser.parse_args()

    stats: Counter = Counter()
    # Two clients on purpose: hubs need browser headers (Linktree 403s bots),
    # but shorteners need NON-browser headers (t.co only 301s non-browsers).
    plain = httpx.Client(headers={"User-Agent": "inkpages/0.1 (link resolver)"})
    with plain, httpx.Client(headers=UA) as client, db.connect() as conn:
        platforms = db.platform_ids(conn)
        # Re-queue hubs whose earlier crawl produced nothing (e.g. was blocked
        # before the browser-header fix) so they get another attempt.
        with conn.cursor() as cur:
            if args.recrawl_all:
                cur.execute(
                    """update accounts a set last_hydrated = null
                       from platforms p
                       where p.id = a.platform_id and p.kind = 'link_hub'
                         and a.status <> 'deleted'""")
            elif args.recrawl_platform:
                cur.execute(
                    """update accounts a set last_hydrated = null
                       from platforms p
                       where p.id = a.platform_id and p.slug = %s
                         and a.status <> 'deleted'""",
                    (args.recrawl_platform,))
            else:
                # Weekly, not every run: a JS-rendered hub (Carrd) crawls
                # clean but yields 0 links forever — re-nulling those each
                # run burned ~60 fetches of the hub budget on known-empty
                # pages ahead of never-crawled ones.
                cur.execute(
                    """update accounts a set last_hydrated = null
                       from platforms p
                       where p.id = a.platform_id and p.kind = 'link_hub'
                         and a.last_hydrated is not null and a.status <> 'deleted'
                         and a.last_hydrated < now() - interval '7 days'
                         and not exists (select 1 from identity_edges e
                                         where e.source_account_id = a.id)""")
        resolve_shorteners(conn, plain, platforms, stats)
        crawl_hubs(conn, client, platforms, args.max_hubs, stats)
        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
