"""Resolve artist-published shortened links (t.co etc.) and crawl link-hub
pages (Linktree, Carrd, potofu.me, lit.link) to extract the links inside.

Both are the artist's own published surfaces, so results become ordinary
identity_edges with the fetched page snapshot as evidence. Twitter itself is
never fetched (t.co is a redirect hop, not Twitter content); display-only
platforms are never fetched.

Usage: uv run python -m inkpages.crawl_links [--max-hubs 100]
"""
import argparse
import time
from collections import Counter

import httpx
from psycopg.rows import dict_row

from . import db
from .extract import find_platform_links, find_short_links

HUB_PLATFORMS = ("linktree", "carrd", "potofu", "litlink")
UA = {"User-Agent": "inkpages/0.1 (no-AI artist directory; link verification)"}


def resolve_url(client: httpx.Client, url: str, cache: dict) -> str | None:
    if url in cache:
        return cache[url]
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
    cache[url] = final
    return final


def resolve_shorteners(conn, client, platforms, stats):
    """Latest snapshot per account containing shortener links -> resolve ->
    platform links become edges with the shortener chain as evidence_url."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select distinct on (s.account_id)
                      s.account_id, s.id as snapshot_id, s.bio_text
               from account_snapshots s
               where s.bio_text ~* '(t\\.co|bit\\.ly|tinyurl\\.com|goo\\.gl)/'
               order by s.account_id, s.captured_at desc"""
        )
        rows = cur.fetchall()
    cache: dict = {}
    for row in rows:
        for short in find_short_links(row["bio_text"]):
            final = resolve_url(client, short, cache)
            stats["shorteners_resolved"] += 1
            if not final:
                stats["shorteners_failed"] += 1
                continue
            for link in find_platform_links(final):
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
                               matched_text=None)
                stats["edges_from_shorteners"] += 1


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
        time.sleep(0.3)
        try:
            resp = client.get(hub["profile_url"], follow_redirects=True, timeout=20)
        except httpx.HTTPError:
            stats["hubs_failed"] += 1
            continue
        with conn.cursor() as cur:
            if resp.status_code == 404:
                cur.execute("update accounts set status = 'deleted', last_hydrated = now() where id = %s",
                            (hub["id"],))
                stats["hubs_404"] += 1
                continue
            cur.execute("update accounts set status = 'active', last_hydrated = now() where id = %s",
                        (hub["id"],))
        html = resp.text[:500_000]
        links = [l for l in find_platform_links(html)
                 if not (l.platform == hub["platform"])]
        snapshot_id = db.insert_snapshot(
            conn, hub["id"],
            bio_text="\n".join(sorted({l.url for l in links})) or None,
            display_name=None, followers_count=None, following_count=None,
            raw={"url": hub["profile_url"], "status": resp.status_code,
                 "link_count": len(links)},
            fetch_source="hub_crawl",
        )
        stats["hubs_crawled"] += 1
        for link in links:
            platform_id = platforms.get(link.platform)
            if platform_id is None:
                continue
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
                           evidence_url=link.url, matched_text=None)
            stats["edges_from_hubs"] += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-hubs", type=int, default=100)
    args = parser.parse_args()

    stats: Counter = Counter()
    with httpx.Client(headers=UA) as client, db.connect() as conn:
        platforms = db.platform_ids(conn)
        resolve_shorteners(conn, client, platforms, stats)
        crawl_hubs(conn, client, platforms, args.max_hubs, stats)
        conn.commit()
    print("done:", dict(stats))


if __name__ == "__main__":
    main()
